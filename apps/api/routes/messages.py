"""用户唯一可见的消息入口。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from apps.api.context import ApiContext
from apps.api.run_interactions import RunInteractionService
from apps.api.schemas import MessageCreate, RunCreate, UnifiedMessageCreate
from yuwang.chat import encode_chat_event
from yuwang.dispatch import ActiveMessageRoute, route_active_message
from yuwang.domain.models import ACTIVE_RUN_STATUSES


def _stream(event_type: str, payload: dict[str, object]) -> AsyncIterator[str]:
    async def events() -> AsyncIterator[str]:
        yield encode_chat_event(event_type, payload)

    return events()


def _response(event_type: str, payload: dict[str, object]) -> StreamingResponse:
    return StreamingResponse(
        _stream(event_type, payload),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _replay_existing_request(
    context: ApiContext,
    interactions: RunInteractionService,
    thread_id: UUID,
    body: UnifiedMessageCreate,
) -> StreamingResponse | None:
    """重放同一 request_id 的既有结果，不按当前 Run 状态重新分流。

    网络重连时原请求可能已经让 Run 完成或停止；若仅依据“现在是否有活跃
    Run”判断，就会把重发误认为新的聊天或第二个任务。每种受控输入都有已
    持久化的来源关系，先查询它们再做新的意图判断。
    """

    repository = context.repository
    runs = repository.list_runs(thread_id)
    for run in reversed(runs):
        if run.stop_request_id == body.request_id:
            message = repository.get_message(body.request_id)
            return _response(
                "execution_stopped",
                {
                    "run": run.model_dump(mode="json"),
                    "user_message": message.model_dump(mode="json") if message else None,
                },
            )
        task = repository.get_run_task(run.id)
        if task and task.origin_message_id == body.request_id:
            if task.body != body.content or task.artifact_ids != body.artifact_ids:
                raise HTTPException(409, "请求 ID 已用于不同的任务内容")
            message = repository.get_message(body.request_id)
            return _response(
                "execution_started",
                {
                    "run": run.model_dump(mode="json"),
                    "user_message": message.model_dump(mode="json") if message else None,
                },
            )

    guidance_request = repository.find_guidance_request(thread_id, body.request_id)
    if guidance_request:
        run, guidance = guidance_request
        if guidance.content != body.content or guidance.artifact_ids != body.artifact_ids:
            raise HTTPException(409, "请求 ID 已用于不同的追加指引")
        message = repository.get_message(body.request_id)
        return _response(
            "guidance_queued",
            {
                "run": run.model_dump(mode="json"),
                "guidance": guidance.model_dump(mode="json"),
                "user_message": message.model_dump(mode="json") if message else None,
            },
        )

    control_request = repository.find_control_request(thread_id, body.request_id)
    if not control_request:
        return None
    run, action, payload_hash = control_request
    if payload_hash != interactions.payload_hash(body.content, body.artifact_ids):
        raise HTTPException(409, "请求 ID 已用于不同的补充内容")
    event_type = {
        "input": "input_received",
        "clarification": "clarification_received",
    }.get(action)
    if not event_type:
        return None
    replayed = interactions.replay_control(
        run,
        action,
        body.request_id,
        body.content,
        body.artifact_ids,
    )
    return _response(
        event_type,
        {
            "run": replayed.run.model_dump(mode="json"),
            "user_message": (
                replayed.message.model_dump(mode="json") if replayed.message else None
            ),
        },
    )


def create_message_router(context: ApiContext) -> APIRouter:
    """让 Web 只发送一条消息，由服务端按 Run 状态决定实际动作。"""

    router = APIRouter(prefix="/api/v1", tags=["messages"])
    interactions = RunInteractionService(context)

    @router.post("/threads/{thread_id}/message")
    async def send_message(thread_id: UUID, body: UnifiedMessageCreate) -> StreamingResponse:
        replayed = _replay_existing_request(context, interactions, thread_id, body)
        if replayed:
            return replayed
        active = [
            run
            for run in context.repository.list_runs(thread_id)
            if run.status in ACTIVE_RUN_STATUSES
        ]
        run = active[-1] if active else None
        decision: ActiveMessageRoute | Literal["run"]
        if run:
            decision = route_active_message(body.content, run.status)
        else:
            context.validate_user_message_artifacts(thread_id, body.artifact_ids)
            decision = "run"
        if decision == "stop":
            user_message = (
                context.save_user_message(
                    thread_id,
                    MessageCreate(content=body.content, artifact_ids=body.artifact_ids),
                    message_id=body.request_id,
                    allow_active_competition=True,
                )
                if run
                else None
            )
            stopped = context.stop_run(run.id, request_id=body.request_id) if run else None
            return _response(
                "execution_stopped",
                {
                    "run": stopped.model_dump(mode="json") if stopped else None,
                    "user_message": user_message.model_dump(mode="json") if user_message else None,
                },
            )
        if decision == "run":
            # 不再注入 `regex: .+`。没有确定性规则时引擎会完成回答但明确标记为
            # “未外部验证”；规则由版本化 Agent 配置或兼容 API 的严格输入提供。
            user_message = context.save_user_message(
                thread_id,
                MessageCreate(content=body.content, artifact_ids=body.artifact_ids),
                message_id=body.request_id,
            )
            created = await context.start_run(
                thread_id,
                RunCreate(
                    provider_config_id=body.provider_config_id,
                    authorized_targets=body.authorized_targets,
                ),
                origin_message=user_message,
            )
            return _response(
                "execution_started",
                {
                    "run": created.model_dump(mode="json"),
                    "user_message": user_message.model_dump(mode="json"),
                },
            )
        if not run:  # 新消息只会走 chat/run/clarify；其余分支必须存在活动 Run。
            return _response("reply_failed", {"message": "当前没有可处理的运行", "retryable": True})
        if decision == "guidance":
            result = interactions.queue_guidance(
                run.id, body.content, body.request_id, body.artifact_ids
            )
            return _response(
                "guidance_queued",
                {
                    "run": result.run.model_dump(mode="json"),
                    "guidance": result.guidance.model_dump(mode="json") if result.guidance else None,
                    "user_message": result.message.model_dump(mode="json") if result.message else None,
                },
            )
        if decision == "input":
            result = interactions.submit_input(
                run.id, body.content, body.request_id, body.artifact_ids
            )
            return _response(
                "input_received",
                {
                    "run": result.run.model_dump(mode="json"),
                    "user_message": result.message.model_dump(mode="json") if result.message else None,
                },
            )
        result = interactions.submit_clarification(
            run.id, body.content, body.request_id, body.artifact_ids
        )
        return _response(
            "clarification_received",
            {
                "run": result.run.model_dump(mode="json"),
                "user_message": result.message.model_dump(mode="json") if result.message else None,
            },
        )

    return router
