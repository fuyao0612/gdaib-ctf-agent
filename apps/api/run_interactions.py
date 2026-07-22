"""统一消息入口与旧 Run 路由共用的人工介入用例。

文本输入在不同运行状态下含义不同，但持久化、幂等和恢复必须只有一套规则。这里
不暴露 HTTP 细节，路由只把请求转换为参数并选择 SSE 或 JSON 响应。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException

from apps.api.context import ApiContext
from apps.api.schemas import MessageCreate
from yuwang.agent import AgentEngine, AgentStateModel
from yuwang.control import RunGuidance
from yuwang.domain.models import (
    EventType,
    MemoryRecord,
    Message,
    MessageRole,
    Run,
    RunStatus,
    TaskSpec,
)
from yuwang.settings import AgentProfileVersion


@dataclass(frozen=True)
class RunInteraction:
    """一次统一输入处理后的公开结果，`message` 为首次写入的时间线记录。"""

    run: Run
    message: Message | None = None
    guidance: RunGuidance | None = None


@dataclass(frozen=True)
class ResumeDependencies:
    """已在状态转换前验证过的恢复依赖。"""

    task: TaskSpec
    profile: AgentProfileVersion
    engine: AgentEngine


class RunInteractionService:
    """处理追加指引、补充信息和 Task Brief 澄清，统一恢复检查点。"""

    def __init__(self, context: ApiContext) -> None:
        self.context = context
        self.repository = context.repository

    @staticmethod
    def payload_hash(content: str, artifact_ids: list[UUID]) -> str:
        payload = {"content": content, "artifact_ids": [str(value) for value in artifact_ids]}
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _prepare_resume(self, run: Run) -> ResumeDependencies:
        """先验证并构造恢复依赖，再允许持久化层把 Run 改为 running。"""

        task = self.repository.get_run_task(run.id)
        provider_configs = self.repository.get_provider_snapshot(run.id)
        profile = self.repository.get_run_agent_profile(run.id)
        if not task or not provider_configs or not profile:
            raise HTTPException(409, "恢复所需快照不完整")
        provider = self.context.build_provider_chain(provider_configs)
        engine = AgentEngine(
            self.repository,
            provider,
            self.context.registry,
            self.context.policy,
            profile=profile,
            artifact_root=self.context.config.artifact_root,
        )
        return ResumeDependencies(task=task, profile=profile, engine=engine)

    def _schedule_resume(self, run: Run, dependencies: ResumeDependencies) -> None:
        """只在没有活跃本地任务时调度；失败后可由同一 request_id 重放。"""

        existing = self.context.tasks.get(run.id)
        if existing and not existing.done():
            return
        coroutine = dependencies.engine.resume(run.id, dependencies.task)
        try:
            self.context.schedule(run.id, coroutine)
        except Exception as exc:
            coroutine.close()
            raise HTTPException(503, "恢复调度失败，请使用相同请求 ID 重试") from exc

    def _resume_if_needed(
        self,
        run: Run,
        dependencies: ResumeDependencies | None = None,
    ) -> None:
        """已提交的交互重放时补调度，不会重复启动仍在运行的协程。"""

        if run.status != RunStatus.RUNNING:
            return
        existing = self.context.tasks.get(run.id)
        if existing and not existing.done():
            return
        self._schedule_resume(run, dependencies or self._prepare_resume(run))

    def _replay_existing_control(
        self,
        run: Run,
        *,
        action: str,
        request_id: UUID,
        content: str,
        artifact_ids: list[UUID],
    ) -> RunInteraction | None:
        """返回既有控制请求，并在首个调度失败后用同一 ID 安全补恢复。"""

        existing = self.repository.find_control_request(run.thread_id, request_id)
        if not existing:
            return None
        recorded_run, recorded_action, recorded_hash = existing
        if recorded_run.id != run.id:
            raise HTTPException(409, "请求 ID 已用于当前会话中的其他运行")
        if (
            recorded_action != action
            or recorded_hash != self.payload_hash(content, artifact_ids)
        ):
            raise HTTPException(409, "请求 ID 已用于不同的控制操作")
        message = self.repository.get_message(request_id)
        if not message:
            raise HTTPException(409, "幂等控制请求缺少消息记录")
        self._resume_if_needed(recorded_run)
        return RunInteraction(run=recorded_run, message=message)

    def replay_control(
        self,
        run: Run,
        action: str,
        request_id: UUID,
        content: str,
        artifact_ids: list[UUID],
    ) -> RunInteraction:
        """供统一消息 SSE 重放调用；不存在完整交互记录时拒绝伪造成功。"""

        replayed = self._replay_existing_control(
            run,
            action=action,
            request_id=request_id,
            content=content,
            artifact_ids=artifact_ids,
        )
        if not replayed:
            raise HTTPException(409, "控制请求记录不存在")
        return replayed

    @staticmethod
    def _add_artifacts(state: AgentStateModel, artifact_ids: list[UUID]) -> None:
        for artifact_id in artifact_ids:
            if artifact_id not in state.supplemental_artifact_ids:
                state.supplemental_artifact_ids.append(artifact_id)

    def queue_guidance(
        self,
        run_id: UUID,
        content: str,
        request_id: UUID,
        artifact_ids: list[UUID] | None = None,
    ) -> RunInteraction:
        artifact_ids = artifact_ids or []
        run = self.context.require_run(run_id)
        if run.status not in {
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            RunStatus.PAUSED,
            RunStatus.WAITING_APPROVAL,
        }:
            raise HTTPException(409, "当前状态不能追加指引")
        self.context.validate_user_message_artifacts(run.thread_id, artifact_ids)
        # 先固化时间线消息。若后续队列写入意外失败，用户仍能用同一 request_id
        # 重试；反过来先入队会留下“指引可能已生效、但时间线没有该消息”的孤儿记录。
        message = self.context.save_user_message(
            run.thread_id,
            MessageCreate(content=content, artifact_ids=artifact_ids),
            message_id=request_id,
        )
        try:
            guidance, claimed = self.repository.queue_guidance(
                run_id, request_id, content, artifact_ids
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not claimed:
            return RunInteraction(run=run, message=message, guidance=guidance)
        self.repository.create_event(
            run_id,
            EventType.GUIDANCE_QUEUED,
            "追加指引已排队",
            {"sequence": guidance.sequence, "content_length": len(content), "artifact_count": len(artifact_ids)},
        )
        return RunInteraction(run=run, message=message, guidance=guidance)

    def submit_input(
        self,
        run_id: UUID,
        content: str,
        request_id: UUID,
        artifact_ids: list[UUID] | None = None,
    ) -> RunInteraction:
        artifact_ids = artifact_ids or []
        run = self.context.require_run(run_id)
        replayed = self._replay_existing_control(
            run,
            action="input",
            request_id=request_id,
            content=content,
            artifact_ids=artifact_ids,
        )
        if replayed:
            return replayed
        if run.status != RunStatus.WAITING_INPUT:
            raise HTTPException(409, "运行当前不在等待补充状态")
        self.context.validate_user_message_artifacts(run.thread_id, artifact_ids)
        checkpoint = self.repository.latest_checkpoint(run.id)
        if not checkpoint:
            raise HTTPException(409, "补充恢复所需检查点不完整")
        state = AgentStateModel.model_validate(checkpoint.state)
        dependencies = self._prepare_resume(run)
        if len(state.supplemental_inputs) >= dependencies.profile.intervention_policy.max_requests:
            raise HTTPException(409, "人工补充次数已达到配置上限")
        payload_hash = self.payload_hash(content, artifact_ids)
        message = Message(
            id=request_id,
            thread_id=run.thread_id,
            role=MessageRole.USER,
            content=content,
            artifact_ids=artifact_ids,
        )
        memory = (
            MemoryRecord(
                thread_id=run.thread_id,
                source_run_id=run.id,
                kind="user_input",
                content=content,
            )
            if dependencies.profile.memory_policy.enabled
            else None
        )
        state.supplemental_inputs.append(content)
        self._add_artifacts(state, artifact_ids)
        state.action = None
        try:
            run, claimed, persisted_message = self.repository.commit_run_interaction(
                run_id=run_id,
                request_id=request_id,
                action="input",
                payload_hash=payload_hash,
                expected_status=RunStatus.WAITING_INPUT,
                message=message,
                checkpoint_node="input_received",
                checkpoint_state=state.model_dump(mode="json"),
                event_type=EventType.INPUT_RECEIVED,
                event_summary="已接收用户补充，准备从检查点继续",
                event_payload={
                    "input_length": len(content),
                    "artifact_count": len(artifact_ids),
                },
                memory=memory,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        self._resume_if_needed(run, dependencies if claimed else None)
        return RunInteraction(run=run, message=persisted_message)

    def submit_clarification(
        self,
        run_id: UUID,
        content: str,
        request_id: UUID,
        artifact_ids: list[UUID] | None = None,
        expected_brief_version: int | None = None,
    ) -> RunInteraction:
        artifact_ids = artifact_ids or []
        run = self.context.require_run(run_id)
        replayed = self._replay_existing_control(
            run,
            action="clarification",
            request_id=request_id,
            content=content,
            artifact_ids=artifact_ids,
        )
        if replayed:
            return replayed
        if run.status != RunStatus.WAITING_CLARIFICATION:
            raise HTTPException(409, "运行当前不在等待任务澄清状态")
        brief = self.repository.latest_task_brief(run_id)
        if not brief or (
            expected_brief_version is not None and brief.version != expected_brief_version
        ):
            raise HTTPException(409, "Task Brief 版本已变化，请刷新后重试")
        self.context.validate_user_message_artifacts(run.thread_id, artifact_ids)
        checkpoint = self.repository.latest_checkpoint(run_id)
        if not checkpoint:
            raise HTTPException(409, "澄清恢复检查点缺失")
        state = AgentStateModel.model_validate(checkpoint.state)
        payload_hash = self.payload_hash(content, artifact_ids)
        dependencies = self._prepare_resume(run)
        message = Message(
            id=request_id,
            thread_id=run.thread_id,
            role=MessageRole.USER,
            content=content,
            artifact_ids=artifact_ids,
        )
        state.supplemental_inputs.append(content)
        self._add_artifacts(state, artifact_ids)
        state.action = None
        try:
            run, claimed, persisted_message = self.repository.commit_run_interaction(
                run_id=run_id,
                request_id=request_id,
                action="clarification",
                payload_hash=payload_hash,
                expected_status=RunStatus.WAITING_CLARIFICATION,
                expected_brief_version=expected_brief_version,
                message=message,
                checkpoint_node="clarification_received",
                checkpoint_state=state.model_dump(mode="json"),
                event_type=EventType.CLARIFICATION_RECEIVED,
                event_summary="已接收澄清信息，正在更新 Task Brief",
                event_payload={
                    "brief_version": brief.version,
                    "input_length": len(content),
                    "artifact_count": len(artifact_ids),
                },
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        self._resume_if_needed(run, dependencies if claimed else None)
        return RunInteraction(run=run, message=persisted_message)
