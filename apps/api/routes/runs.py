"""运行调度、恢复、审计与 SSE 事件路由。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from apps.api.context import ApiContext
from apps.api.run_interactions import RunInteractionService
from apps.api.schemas import (
    ClarificationSubmit,
    ControlRequest,
    GuidanceSubmit,
    MessageCreate,
    PlanDecision,
    PlanEdit,
    RunCreate,
    RunInput,
    TurnCreate,
)
from yuwang.agent import AgentEngine, AgentStateModel
from yuwang.control import PlanRevision, PlanSource, RunGuidance
from yuwang.domain.models import (
    ACTIVE_RUN_STATUSES,
    EventType,
    Run,
    RunStatus,
)


def create_run_router(context: ApiContext) -> APIRouter:
    """创建 Run 生命周期和只读审计路由。"""

    router = APIRouter(prefix="/api/v1", tags=["runs"])
    repository = context.repository
    interactions = RunInteractionService(context)

    @router.post("/threads/{thread_id}/runs", response_model=Run, status_code=202)
    async def start_run(
        thread_id: UUID,
        body: RunCreate = Body(default_factory=RunCreate),
    ) -> Run:
        return await context.start_run(thread_id, body)

    @router.post("/threads/{thread_id}/turns", response_model=Run, status_code=202)
    async def send_turn(thread_id: UUID, body: TurnCreate) -> Run:
        """保存用户消息并自动创建 Run，让调用方只理解“发送一轮对话”。"""

        user_message = context.save_user_message(
            thread_id,
            MessageCreate(content=body.content, artifact_ids=body.artifact_ids),
        )
        return await context.start_run(
            thread_id,
            RunCreate(
                provider_config_id=body.provider_config_id,
                authorized_targets=body.authorized_targets,
                success_conditions=body.success_conditions,
                verification_rules=body.verification_rules,
                plan_mode=body.plan_mode,
            ),
            origin_message=user_message,
        )

    def recovery_data(run: Run) -> tuple[Any, list[Any], Any]:
        task_spec = repository.get_run_task(run.id)
        provider_configs = repository.get_provider_snapshot(run.id)
        profile = repository.get_run_agent_profile(run.id)
        if not task_spec or not provider_configs or not profile:
            raise HTTPException(409, "控制恢复所需快照不完整")
        return task_spec, provider_configs, profile

    def schedule_resume(run: Run) -> None:
        task_spec, provider_configs, profile = recovery_data(run)
        provider = context.build_provider_chain(provider_configs)
        engine = AgentEngine(
            repository,
            provider,
            context.registry,
            context.policy,
            profile=profile,
            artifact_root=context.config.artifact_root,
        )
        context.schedule(run.id, engine.resume(run.id, task_spec))

    @router.get("/runs/{run_id}/control")
    async def get_run_control(run_id: UUID) -> dict[str, Any]:
        run = context.require_run(run_id)
        return {
            "status": run.status,
            "plan_mode": run.plan_mode,
            "task_briefs": [
                value.model_dump(mode="json")
                for value in repository.list_task_briefs(run_id)
            ],
            "plans": [
                value.model_dump(mode="json")
                for value in repository.list_plan_revisions(run_id)
            ],
            "guidance": [
                value.model_dump(mode="json") for value in repository.list_guidance(run_id)
            ],
        }

    @router.post("/runs/{run_id}/pause", response_model=Run, status_code=202)
    async def pause_run(run_id: UUID, body: ControlRequest) -> Run:
        context.require_run(run_id)
        try:
            run, claimed = repository.request_pause(run_id, body.request_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        if claimed:
            repository.create_event(
                run_id,
                EventType.PAUSE_REQUESTED,
                "暂停请求已排队，将在安全检查点生效",
                {},
            )
        return run

    @router.post("/runs/{run_id}/resume", response_model=Run, status_code=202)
    async def resume_run(run_id: UUID, body: ControlRequest) -> Run:
        payload_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        try:
            run, claimed = repository.claim_run_control(
                run_id, body.request_id, "resume", payload_hash, RunStatus.PAUSED
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        if claimed:
            repository.create_event(
                run_id, EventType.RUN_RESUMED, "运行已从暂停检查点继续", {}
            )
            schedule_resume(run)
        return run

    @router.post("/runs/{run_id}/guidance", response_model=RunGuidance, status_code=202)
    async def queue_guidance(run_id: UUID, body: GuidanceSubmit) -> RunGuidance:
        result = interactions.queue_guidance(
            run_id, body.content, body.request_id, body.artifact_ids
        )
        if not result.guidance:  # 防御式检查，服务成功时一定返回指引记录。
            raise HTTPException(409, "追加指引记录缺失")
        return result.guidance

    @router.post("/runs/{run_id}/clarification", response_model=Run, status_code=202)
    async def submit_clarification(run_id: UUID, body: ClarificationSubmit) -> Run:
        return interactions.submit_clarification(
            run_id,
            body.content,
            body.request_id,
            body.artifact_ids,
            body.expected_brief_version,
        ).run

    @router.put("/runs/{run_id}/plan", response_model=PlanRevision)
    async def edit_plan(run_id: UUID, body: PlanEdit) -> PlanRevision:
        run = context.require_run(run_id)
        if run.status != RunStatus.WAITING_APPROVAL:
            raise HTTPException(409, "运行当前不在等待计划确认状态")
        revision = PlanRevision(
            run_id=run_id,
            version=body.expected_version + 1,
            plan=body.plan,
            source=PlanSource.USER_EDIT,
            change_reason=body.reason,
            based_on_version=body.expected_version,
        )
        payload_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        try:
            revision, claimed = repository.save_user_plan_revision(
                revision, body.request_id, payload_hash
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if claimed:
            checkpoint = repository.latest_checkpoint(run_id)
            if not checkpoint:
                raise HTTPException(409, "计划编辑检查点缺失")
            state = AgentStateModel.model_validate(checkpoint.state)
            state.plan = revision.plan
            repository.save_checkpoint(run_id, "plan_edited", state.model_dump(mode="json"))
            repository.create_event(
                run_id,
                EventType.PLAN_EDITED,
                "用户已编辑执行计划",
                {"version": revision.version, "based_on_version": body.expected_version},
            )
        return revision

    async def decide_plan(run_id: UUID, body: PlanDecision, approved: bool) -> Run:
        run = context.require_run(run_id)
        action = "plan_approve" if approved else "plan_reject"
        payload_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        try:
            run, claimed = repository.claim_run_control(
                run_id,
                body.request_id,
                action,
                payload_hash,
                RunStatus.WAITING_APPROVAL,
                body.expected_version,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        if not claimed:
            return run
        checkpoint = repository.latest_checkpoint(run_id)
        revision = repository.latest_plan_revision(run_id)
        if not checkpoint or not revision:
            raise HTTPException(409, "计划确认恢复数据缺失")
        state = AgentStateModel.model_validate(checkpoint.state)
        state.plan = revision.plan
        state.plan_approved = approved
        node = "plan_approved" if approved else "plan_rejected"
        if not approved:
            state.supplemental_inputs.append(f"计划拒绝原因：{body.reason or '请重新规划'}")
        repository.save_checkpoint(run_id, node, state.model_dump(mode="json"))
        repository.create_event(
            run_id,
            EventType.PLAN_APPROVED if approved else EventType.PLAN_REJECTED,
            "用户已批准执行计划" if approved else "用户已拒绝计划，正在重新规划",
            {"version": revision.version, "has_reason": bool(body.reason)},
        )
        schedule_resume(run)
        return run

    @router.post("/runs/{run_id}/plan/approve", response_model=Run, status_code=202)
    async def approve_plan(run_id: UUID, body: PlanDecision) -> Run:
        return await decide_plan(run_id, body, True)

    @router.post("/runs/{run_id}/plan/reject", response_model=Run, status_code=202)
    async def reject_plan(run_id: UUID, body: PlanDecision) -> Run:
        return await decide_plan(run_id, body, False)

    @router.get("/runs/{run_id}", response_model=Run)
    async def get_run(run_id: UUID) -> Run:
        return context.require_run(run_id)

    @router.post("/runs/{run_id}/stop", response_model=Run)
    async def stop_run(run_id: UUID) -> Run:
        return context.stop_run(run_id)

    @router.post("/runs/{run_id}/input", response_model=Run, status_code=202)
    async def submit_run_input(run_id: UUID, body: RunInput) -> Run:
        # 旧接口没有请求 ID 时仍可调用；统一消息入口始终提供 UUID 并获得幂等保护。
        request_id = body.request_id or uuid4()
        return interactions.submit_input(
            run_id, body.content, request_id, body.artifact_ids
        ).run

    @router.post("/runs/{run_id}/retry", response_model=Run, status_code=202)
    async def retry_run(run_id: UUID) -> Run:
        previous = context.require_run(run_id)
        if previous.status not in {RunStatus.FAILED, RunStatus.STOPPED}:
            raise HTTPException(409, "仅失败或停止的运行可重试")
        thread = context.require_thread(previous.thread_id)
        task = repository.get_run_task(previous.id)
        if not task:
            raise HTTPException(409, "原运行缺少 TaskSpec 快照，无法安全重试")
        try:
            provider_configs = repository.get_provider_snapshot(previous.id)
            if not provider_configs:
                raise ValueError("原运行缺少 Provider 快照")
            provider = context.build_provider_chain(provider_configs)
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc
        profile = repository.get_run_agent_profile(
            previous.id
        ) or context.profile_service.resolve(None)
        retried = Run(
            thread_id=thread.id,
            provider=previous.provider,
            provider_config_id=previous.provider_config_id,
            agent_profile_id=profile.profile_id,
            agent_profile_version=profile.version,
            attempt=previous.attempt + 1,
        )
        repository.save_run(retried)
        repository.save_run_task(retried.id, task)
        repository.save_provider_snapshot(retried.id, provider_configs)
        repository.save_run_agent_profile(retried.id, profile)
        checkpoint = repository.latest_checkpoint(previous.id)
        initial_state = AgentStateModel.model_validate(checkpoint.state) if checkpoint else None
        context.schedule(
            retried.id,
            context.execute(retried, task, provider, profile, initial_state),
        )
        return retried

    @router.get("/runs/{run_id}/events")
    async def list_events(
        run_id: UUID,
        after: int = Query(0, ge=0),
    ) -> list[dict[str, Any]]:
        context.require_run(run_id)
        return [
            event.model_dump(mode="json") for event in repository.list_events(run_id, after)
        ]

    @router.get("/runs/{run_id}/audit")
    async def run_audit(run_id: UUID) -> dict[str, Any]:
        run = context.require_run(run_id)
        checkpoint = repository.latest_checkpoint(run_id)
        profile = repository.get_run_agent_profile(run_id)
        state = checkpoint.state if checkpoint else {}
        task_spec = repository.get_run_task(run_id)
        budget = task_spec.budget if task_spec else None
        return {
            "run": {
                "provider": run.provider,
                "agent_profile_id": str(run.agent_profile_id) if run.agent_profile_id else None,
                "agent_profile_version": run.agent_profile_version,
                "validation_status": run.validation_status,
                "evidence_level": run.evidence_level,
            },
            "usage": {
                "steps": state.get("step", 0),
                "model_calls": state.get("model_calls", 0),
                "tool_calls": state.get("tool_calls", 0),
                "tokens": state.get("tokens", 0),
                "model_cost": state.get("model_cost", 0),
                "elapsed_seconds": state.get("elapsed_seconds", 0),
                "context_tokens": state.get("context_tokens", 0),
                "observation_chars": state.get("observation_chars", 0),
                "context_truncations": state.get("context_truncations", 0),
            },
            "limits": budget.model_dump(mode="json") if budget else {},
            "profile": (
                {
                    "name": profile.name,
                    "version": profile.version,
                    "completion_mode": profile.completion_mode,
                    "planning_strategy": profile.planning_strategy,
                    "workflow_preset": profile.workflow.preset,
                    "default_provider_id": (
                        str(profile.default_provider_id) if profile.default_provider_id else None
                    ),
                    "fallback_provider_ids": [
                        str(value) for value in profile.fallback_provider_ids
                    ],
                    "context_policy": profile.context_policy.model_dump(mode="json"),
                    "memory_policy": profile.memory_policy.model_dump(mode="json"),
                    "intervention_policy": profile.intervention_policy.model_dump(mode="json"),
                }
                if profile
                else None
            ),
            "model_calls": [
                value.model_dump(mode="json") for value in repository.list_model_calls(run_id)
            ],
            "tool_calls": [
                value.model_dump(mode="json") for value in repository.list_tool_calls(run_id)
            ],
            "evidence": [
                value.model_dump(mode="json") for value in repository.list_evidence(run_id)
            ],
            "checkpoints": [
                {
                    "checkpoint_sequence": value.checkpoint_sequence,
                    "node": value.node,
                    "state_schema_version": value.state_schema_version,
                    "elapsed_seconds": value.elapsed_seconds,
                    "created_at": value.created_at,
                }
                for value in repository.list_checkpoints(run_id)
            ],
        }

    @router.get("/runs/{run_id}/events/stream")
    async def stream_events(
        run_id: UUID,
        request: Request,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        after: int = Query(0, ge=0),
    ) -> StreamingResponse:
        """从持久化游标之后持续发送事件；返回流本身不保存业务状态。"""

        context.require_run(run_id)
        cursor = max(after, int(last_event_id or 0))

        async def generate() -> AsyncIterator[str]:
            nonlocal cursor
            idle = 0
            while True:
                if await request.is_disconnected():
                    return
                events = repository.list_events(run_id, cursor)
                for event in events:
                    cursor = event.sequence
                    # 使用默认 SSE message 事件，浏览器只需一个 onmessage 处理器；
                    # 领域事件类型仍保留在版本化 JSON 中，新增类型不会破坏旧前端。
                    yield f"id: {event.sequence}\ndata: {event.model_dump_json()}\n\n"
                run = repository.get_run(run_id)
                if (
                    run
                    and run.status not in ACTIVE_RUN_STATUSES
                    and not repository.list_events(run_id, cursor)
                ):
                    return
                if not events:
                    idle += 1
                    if idle % 20 == 0:
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(0.1)
                else:
                    idle = 0

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
