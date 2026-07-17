"""普通聊天上下文与公开流事件，不依赖 AgentAction 或 LangGraph。"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from yuwang.domain.models import Message, MessageRole


def build_chat_messages(
    messages: Iterable[Message], *, recent_limit: int, token_limit: int
) -> list[dict[str, str]]:
    """按消息边界裁剪聊天上下文，避免截断半条 Unicode 文本。"""

    selected = [
        {
            "role": "user" if item.role == MessageRole.USER else "assistant",
            "content": item.content,
        }
        for item in messages
        if item.role in {MessageRole.USER, MessageRole.ASSISTANT, MessageRole.AGENT}
    ][-recent_limit:]
    # 没有厂商 tokenizer 时采用保守字符估算；至少保留最新用户消息。
    while (
        len(selected) > 1
        and sum(len(item["content"]) for item in selected) // 2 > token_limit
    ):
        selected.pop(0)
    return selected


def local_thread_title(content: str, limit: int = 28) -> str:
    """模型标题生成失败时仍能得到稳定、无控制字符的本地标题。"""

    compact = " ".join(content.split()) or "新对话"
    return compact if len(compact) <= limit else f"{compact[:limit].rstrip()}…"


def encode_chat_event(event: str, data: dict[str, Any]) -> str:
    """聊天流使用独立事件名，避免与基于 Run sequence 的 Agent SSE 混淆。"""

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
