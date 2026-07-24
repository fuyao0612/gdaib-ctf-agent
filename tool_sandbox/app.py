"""内部 tool-sandbox 服务，仅允许固定、无 Shell 的结构化操作。"""

from __future__ import annotations

import base64
import re
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class SandboxRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["extract_strings"]
    payload_base64: str = Field(min_length=1, max_length=8_000_000)
    min_length: int = Field(default=4, ge=1, le=1_000)
    max_results: int = Field(default=1_000, ge=1, le=10_000)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/run")
async def run(request: SandboxRunRequest) -> dict[str, object]:
    """只处理内存中的有界数据；不存在命令、路径或可执行代码参数。"""

    try:
        payload = base64.b64decode(request.payload_base64, validate=True)
    except ValueError as exc:
        raise HTTPException(400, "payload_base64 无效") from exc
    if len(payload) > 5 * 1024 * 1024:
        raise HTTPException(413, "沙箱输入超过 5 MiB 限制")
    pattern = re.compile(rb"[\x20-\x7e]{" + str(request.min_length).encode() + rb",}")
    values = [item.decode("ascii", errors="replace") for item in pattern.findall(payload)]
    return {
        "operation": request.operation,
        "strings": values[: request.max_results],
        "truncated": len(values) > request.max_results,
    }
