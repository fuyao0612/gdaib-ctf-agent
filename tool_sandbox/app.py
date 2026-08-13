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
    values = _extract_strings(payload, request.min_length, request.max_results + 1)
    return {
        "operation": request.operation,
        "strings": values[: request.max_results],
        "truncated": len(values) > request.max_results,
    }


def _extract_strings(payload: bytes, minimum: int, maximum: int) -> list[str]:
    """在隔离容器内提取 ASCII 与双端 UTF-16 字符串，并保持稳定去重顺序。"""

    values: list[str] = []
    seen: set[str] = set()
    ascii_pattern = re.compile(rb"[\x20-\x7e]{" + str(minimum).encode() + rb",}")
    for match in ascii_pattern.finditer(payload):
        value = match.group().decode("ascii", errors="replace")
        if value not in seen:
            seen.add(value)
            values.append(value)
            if len(values) >= maximum:
                return values

    # 同一段 UTF-16 字节从奇数偏移解释为相反端序时会产生缺首/缺尾的伪字符串。
    # 先收集两种端序，再丢弃被更长候选完整覆盖的重叠项。
    wide_patterns = (
        (re.compile(rb"(?:[\x20-\x7e]\x00){" + str(minimum).encode() + rb",}"), "utf-16le", 0),
        (re.compile(rb"(?:\x00[\x20-\x7e]){" + str(minimum).encode() + rb",}"), "utf-16be", 1),
    )
    candidates: list[tuple[int, int, int, str]] = []
    for pattern, encoding, order in wide_patterns:
        candidates.extend(
            (match.start(), match.end(), order, match.group().decode(encoding, errors="replace"))
            for match in pattern.finditer(payload)
        )
    for start, end, _order, value in sorted(candidates, key=lambda item: (item[2], item[0])):
        if any(
            other_start <= start
            and other_end >= end
            and other_end - other_start > end - start
            for other_start, other_end, _, _ in candidates
        ):
            continue
        if value not in seen:
            seen.add(value)
            values.append(value)
            if len(values) >= maximum:
                return values
    return values
