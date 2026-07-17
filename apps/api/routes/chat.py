"""普通自然语言聊天路由；不会创建 Run、计划、验证或报告。"""

from __future__ import annotations

import asyncio
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


def create_chat_router(context: ApiContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["chat"])
    repository = context.repository

    def append_attachment_context(
        messages: list[dict[str, str]], body: ChatCreate, defaults: ChatDefaults
    ) -> None:
        """附件仍是不可信用户内容，只读取当前会话内的安全存储引用。"""

        if not messages or not body.artifact_ids:
            return
        sections: list[str] = []
        remaining = defaults.attachment_char_limit
        root = context.config.artifact_root.resolve()
        for artifact_id in body.artifact_ids:
            artifact = repository.get_artifact(artifact_id)
            if not artifact:
                continue
            path = (root / artifact.storage_ref).resolve()
            text = ""
            if root in path.parents and path.is_file() and remaining > 0:
                try:
                    text = path.read_text(encoding="utf-8")[:remaining]
                except UnicodeDecodeError:
                    text = "[二进制附件，仅提供文件元数据]"
            section = f"[不可信附件：{artifact.filename}]\n{text}"
            sections.append(section)
            remaining -= len(text)
        if sections:
            messages[-1]["content"] += "\n\n" + "\n\n".join(sections)

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
        thread = context.require_thread(thread_id)
        if any(run.status in ACTIVE_RUN_STATUSES for run in repository.list_runs(thread_id)):
            raise HTTPException(409, "Agent 任务运行中，请先暂停或结束任务")
        for artifact_id in body.artifact_ids:
            artifact = repository.get_artifact(artifact_id)
            if not artifact or artifact.thread_id != thread_id:
                raise HTTPException(400, "附件引用无效")
        defaults = context.get_settings_service().get_chat_defaults()
        provider_id = body.provider_config_id or defaults.default_provider_id
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
                append_attachment_context(messages, body, defaults)
                if defaults.stream_enabled:
                    async for chunk in provider.stream_text(
                        messages,
                        system_prompt=defaults.system_prompt,
                    ):
                        content += chunk
                        yield encode_chat_event("text_delta", {"text": chunk})
                else:
                    content = await provider.generate_text(
                        messages,
                        system_prompt=defaults.system_prompt,
                    )
                    yield encode_chat_event("text_delta", {"text": content})
                assistant = repository.complete_chat_request(
                    body.request_id, thread_id, content
                )
                yield encode_chat_event(
                    "reply_complete", {"message": assistant.model_dump(mode="json")}
                )
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
                repository.fail_chat_request(body.request_id, "聊天服务处理失败")
                yield encode_chat_event(
                    "reply_failed",
                    {"message": "回复生成失败，请稍后重试", "retryable": True},
                )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
