"""用户唯一可见的消息入口。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from apps.api.context import ApiContext
from apps.api.routes.chat import prepare_chat_stream
from apps.api.schemas import MessageCreate, RunCreate, UnifiedMessageCreate
from yuwang.chat import encode_chat_event
from yuwang.dispatch import route_message
from yuwang.domain.models import ACTIVE_RUN_STATUSES, VerificationRule


def _stream(event_type: str, payload: dict[str, object]) -> AsyncIterator[str]:
    async def events() -> AsyncIterator[str]:
        yield encode_chat_event(event_type, payload)

    return events()


def create_message_router(context: ApiContext) -> APIRouter:
    """让 Web 只发送消息，由后端统一决定自由回复、执行或停止。"""

    router = APIRouter(prefix="/api/v1", tags=["messages"])

    @router.post("/threads/{thread_id}/message")
    async def send_message(thread_id: UUID, body: UnifiedMessageCreate) -> StreamingResponse:
        runs = context.repository.list_runs(thread_id)
        active = [run for run in runs if run.status in ACTIVE_RUN_STATUSES]
        decision = route_message(body.content, bool(active))
        if decision == "chat":
            return StreamingResponse(
                prepare_chat_stream(context, thread_id, body),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        if decision == "stop":
            run = context.stop_run(active[-1].id)
            return StreamingResponse(
                _stream("execution_stopped", {"run": run.model_dump(mode="json")}),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        try:
            user_message = context.save_user_message(
                thread_id,
                MessageCreate(content=body.content, artifact_ids=body.artifact_ids),
            )
            # 统一输入不再向用户暴露“成功答案正则”。证据模式仍需要确定性规则，
            # 因此默认至少验证候选值非空、严格绑定到一次成功受控工具调用；更严格的
            # 业务规则仍由设置中心的 Agent 配置和显式验证策略提供。
            run = await context.start_run(
                thread_id,
                RunCreate(
                    verification_rules=[VerificationRule(kind="regex", value=r".+")]
                ),
            )
        except HTTPException:
            raise
        return StreamingResponse(
            _stream(
                "execution_started",
                {
                    "run": run.model_dump(mode="json"),
                    "user_message": user_message.model_dump(mode="json"),
                },
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
