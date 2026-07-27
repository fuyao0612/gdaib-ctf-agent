"""API 可复用的 Server-Sent Events 编码辅助。"""

from __future__ import annotations

import json
from typing import Any


def encode_sse_event(event: str, data: dict[str, Any]) -> str:
    """编码单个公开 SSE 事件，不承载聊天或 Agent 业务语义。"""

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
