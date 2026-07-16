"""运行调度、恢复、审计与 SSE 事件路由。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from apps.api.context import ApiContext
from apps.api.schemas import MessageCreate, RunCreate, RunInput, TurnCreate
from yuwang.agent import AgentEngine, AgentStateModel
from yuwang.domain.models import (
    ACTIVE_RUN_STATUSES,
    EventType,
    MemoryRecord,
    Message,
    MessageRole,
    Run,
    RunStatus,
)


def create_run_router(context: ApiContext) -> APIRouter:
    """创建 Run 生命周期和只读审计路由。"""

    router = APIRouter(prefix="/api/v1", tags=["runs"])
    repository = context.repository

    async def start(thread_id: UUID, body: RunCreate) -> Run:
        """创建不可变快照并登记后台 Agent；供两个 HTTP 入口共用。"""

        thread = context.require_thread(thread_id)
        profile = context.resolve_thread_profile(thread)
        try:
            selected_id = body.provider_config_id or profile.default_provider_id
            fallback_ids = profile.fallback_provider_ids if profile.default_provider_id else None
            provider_configs, provider = context.resolve_provider_chain(
                selected_id, fallback_ids
            )
            selected = provider_configs[0]
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc
        task = context.build_task(thread, body, profile)
        run = Run(
            thread_id=thread.id,
            provider=selected.name,
            provider_config_id=selected.id,
            agent_profile_id=profile.profile_id,
            agent_profile_version=profile.version,
        )
        try:
            repository.save_run(run)
            repository.save_run_task(run.id, task)
            repository.save_provider_snapshot(run.id, provider_configs)
            repository.save_run_agent_profile(run.id, profile)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        context.schedule(run.id, context.execute(run, task, provider, profile))
        return run

    @router.post("/threads/{thread_id}/runs", response_model=Run, status_code=202)
    async def start_run(
        thread_id: UUID,
        body: RunCreate = Body(default_factory=RunCreate),
    ) -> Run:
        return await start(thread_id, body)

    @router.post("/threads/{thread_id}/turns", response_model=Run, status_code=202)
    async def send_turn(thread_id: UUID, body: TurnCreate) -> Run:
        """保存用户消息并自动创建 Run，让调用方只理解“发送一轮对话”。"""

        context.save_user_message(
            thread_id,
            MessageCreate(content=body.content, artifact_ids=body.artifact_ids),
        )
        return await start(
            thread_id,
            RunCreate(
                provider_config_id=body.provider_config_id,
                authorized_targets=body.authorized_targets,
                success_conditions=body.success_conditions,
                verification_rules=body.verification_rules,
            ),
        )

    @router.get("/runs/{run_id}", response_model=Run)
    async def get_run(run_id: UUID) -> Run:
        return context.require_run(run_id)

    @router.post("/runs/{run_id}/stop", response_model=Run)
    async def stop_run(run_id: UUID) -> Run:
        run = context.require_run(run_id)
        if run.status not in ACTIVE_RUN_STATUSES:
            raise HTTPException(409, "运行已结束")
        stopped = repository.request_stop(run_id)
        task = context.tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        return stopped

    @router.post("/runs/{run_id}/input", response_model=Run, status_code=202)
    async def submit_run_input(run_id: UUID, body: RunInput) -> Run:
        run = context.require_run(run_id)
        if run.status != RunStatus.WAITING_INPUT:
            raise HTTPException(409, "运行当前不在等待补充状态")
        task_spec = repository.get_run_task(run.id)
        checkpoint = repository.latest_checkpoint(run.id)
        provider_configs = repository.get_provider_snapshot(run.id)
        profile = repository.get_run_agent_profile(run.id)
        if not task_spec or not checkpoint or not provider_configs or not profile:
            raise HTTPException(409, "补充恢复所需快照不完整")
        state = AgentStateModel.model_validate(checkpoint.state)
        if len(state.supplemental_inputs) >= profile.intervention_policy.max_requests:
            raise HTTPException(409, "人工补充次数已达到配置上限")
        state.supplemental_inputs.append(body.content)
        state.action = None
        repository.save_message(
            Message(thread_id=run.thread_id, role=MessageRole.USER, content=body.content)
        )
        if profile.memory_policy.enabled:
            repository.save_memory(
                MemoryRecord(
                    thread_id=run.thread_id,
                    source_run_id=run.id,
                    kind="user_input",
                    content=body.content,
                )
            )
        repository.save_checkpoint(run.id, "input_received", state.model_dump(mode="json"))
        run.transition(RunStatus.RUNNING)
        repository.save_run(run)
        repository.create_event(
            run.id,
            EventType.INPUT_RECEIVED,
            "已接收用户补充，准备从检查点继续",
            {"input_length": len(body.content)},
        )
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
        return run

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
