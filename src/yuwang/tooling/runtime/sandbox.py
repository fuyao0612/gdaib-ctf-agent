"""API 到内部 Docker tool-sandbox 的结构化客户端，不提供宿主机降级执行。"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field


class SandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["extract_strings"]
    payload_base64: str = Field(min_length=1, max_length=8_000_000)
    min_length: int = Field(default=4, ge=1, le=1_000)
    max_results: int = Field(default=1_000, ge=1, le=10_000)


class SandboxUnavailable(RuntimeError):
    """Docker 沙箱不可用时明确失败，绝不回退到 API 宿主机。"""


class SandboxRuntime:
    def __init__(self, base_url: str, *, timeout_seconds: float = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3, follow_redirects=False) as client:
                response = await client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def execute(self, request: SandboxRequest) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/v1/run", json=request.model_dump(mode="json")
                )
        except httpx.TimeoutException as exc:
            raise SandboxUnavailable("Docker 工具沙箱响应超时") from exc
        except httpx.HTTPError as exc:
            raise SandboxUnavailable("Docker 工具沙箱不可用，未在宿主机执行") from exc
        if response.status_code != 200:
            raise SandboxUnavailable("Docker 工具沙箱拒绝执行请求")
        body = response.json()
        if not isinstance(body, dict):
            raise SandboxUnavailable("Docker 工具沙箱返回格式无效")
        return body
