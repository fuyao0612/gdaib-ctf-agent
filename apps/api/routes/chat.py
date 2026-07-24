"""普通自然语言聊天路由；不会创建 Run、计划、验证或报告。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from apps.api.context import ApiContext
from apps.api.schemas import ChatCreate
from yuwang.chat import build_chat_messages, encode_chat_event, local_thread_title
from yuwang.domain.models import ACTIVE_RUN_STATUSES, InteractionMode, utcnow
from yuwang.model_providers import ProviderError
from yuwang.settings import ChatDefaults

logger = logging.getLogger(__name__)


def append_attachment_context(
    context: ApiContext,
    messages: list[dict[str, str]],
    body: ChatCreate,
    defaults: ChatDefaults,
) -> None:
    """把当前 Thread 的文本附件作为不可信上下文附给最后一条用户消息。"""

    if not messages or not body.artifact_ids:
        return
    sections: list[str] = []
    remaining = defaults.attachment_char_limit
    root = context.config.artifact_root.resolve()
    for artifact_id in body.artifact_ids:
        artifact = context.repository.get_artifact(artifact_id)
        if not artifact:
            continue
        path = (root / artifact.storage_ref).resolve()
        text = ""
        if root in path.parents and path.is_file() and remaining > 0:
            try:
                text = path.read_text(encoding="utf-8")[:remaining]
            except UnicodeDecodeError:
                text = "[二进制附件，仅提供文件元数据]"
        sections.append(f"[不可信附件：{artifact.filename}]\n{text}")
        remaining -= len(text)
    if sections:
        messages[-1]["content"] += "\n\n" + "\n\n".join(sections)


def prepare_chat_stream(
    context: ApiContext, thread_id: UUID, body: ChatCreate
) -> AsyncIterator[str]:
    """准备自由文本 SSE；统一入口和兼容聊天接口共享此实现。"""

    repository = context.repository
    thread = context.require_thread(thread_id)
    try:
        existing_request = repository.has_chat_request(thread_id, body.request_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if (
        not existing_request
        and any(run.status in ACTIVE_RUN_STATUSES for run in repository.list_runs(thread_id))
    ):
        raise HTTPException(409, "受控任务运行中，请先暂停或结束任务")
    for artifact_id in body.artifact_ids:
        artifact = repository.get_artifact(artifact_id)
        if not artifact or artifact.thread_id != thread_id:
            raise HTTPException(400, "附件引用无效")
    defaults = context.get_settings_service().get_chat_defaults()
    provider_id = (
        body.provider_config_id
        or thread.provider_config_id
        or defaults.default_provider_id
    )
    try:
        _, provider = context.resolve_provider_chain(provider_id)
        user_message, completed = repository.begin_chat_request(
            thread_id,
            body.request_id,
            body.content,
            body.artifact_ids,
            body.retry,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc

    thread.interaction_mode = InteractionMode.CHAT
    if thread.title in {"新对话", "新的对话", "未命名对话"}:
        thread.title = local_thread_title(body.content)
    thread.updated_at = utcnow()
    repository.save_thread(thread)

    async def stream() -> AsyncIterator[str]:
        yield encode_chat_event(
            "reply_start",
            {
                "request_id": str(body.request_id),
                "user_message": user_message.model_dump(mode="json"),
            },
        )
        if completed:
            yield encode_chat_event("text_delta", {"text": completed.content})
            yield encode_chat_event(
                "reply_complete", {"message": completed.model_dump(mode="json")}
            )
            return
        content = ""
        try:
            messages = build_chat_messages(
                repository.list_messages(thread_id),
                recent_limit=defaults.recent_message_limit,
                token_limit=defaults.context_token_limit,
            )
            append_attachment_context(context, messages, body, defaults)
            if defaults.stream_enabled:
                async for chunk in provider.stream_text(messages, system_prompt=defaults.system_prompt):
                    content += chunk
                    yield encode_chat_event("text_delta", {"text": chunk})
            else:
                content = await provider.generate_text(messages, system_prompt=defaults.system_prompt)
                yield encode_chat_event("text_delta", {"text": content})
            assistant = repository.complete_chat_request(body.request_id, thread_id, content)
            yield encode_chat_event("reply_complete", {"message": assistant.model_dump(mode="json")})
        except ProviderError as exc:
            repository.fail_chat_request(body.request_id, str(exc))
            yield encode_chat_event(
                "reply_failed",
                {"message": f"模型回复失败：{exc}", "retryable": exc.retryable},
            )
        except asyncio.CancelledError:
            repository.fail_chat_request(body.request_id, "用户停止生成或连接中断")
            raise
        except Exception:
            # 除 ProviderError 外的异常也必须留下服务端堆栈；客户端只显示安全的
            # 通用提示，避免把内部实现或敏感配置暴露到对话界面。
            logger.exception("聊天流处理失败，request_id=%s", body.request_id)
            repository.fail_chat_request(body.request_id, "聊天服务处理失败")
            yield encode_chat_event(
                "reply_failed", {"message": "回复生成失败，请稍后重试", "retryable": True}
            )

    return stream()


def create_chat_router(context: ApiContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["chat"])
    @router.get(
        "/admin/settings/chat",
        response_model=ChatDefaults,
        dependencies=[Depends(context.require_admin)],
    )
    async def get_chat_defaults() -> ChatDefaults:
        return context.get_settings_service().get_chat_defaults()

    @router.get("/settings/chat", response_model=ChatDefaults)
    async def get_workbench_chat_defaults() -> ChatDefaults:
        return context.get_settings_service().get_chat_defaults()

    @router.put(
        "/admin/settings/chat",
        response_model=ChatDefaults,
        dependencies=[Depends(context.require_admin)],
    )
    async def save_chat_defaults(body: ChatDefaults) -> ChatDefaults:
        return context.get_settings_service().save_chat_defaults(body)

    @router.post("/threads/{thread_id}/chat")
    async def chat(thread_id: UUID, body: ChatCreate) -> StreamingResponse:
        return StreamingResponse(
            prepare_chat_stream(context, thread_id, body),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
