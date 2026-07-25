"""内部 Docker 沙箱的结构化操作与不可用边界测试。"""

from __future__ import annotations

import base64

import httpx
import pytest
from fastapi.testclient import TestClient

from tool_sandbox.app import app
from yuwang.tooling.runtime import SandboxRequest, SandboxRuntime, SandboxUnavailable


def test_sandbox_accepts_only_fixed_structured_operation() -> None:
    client = TestClient(app)
    payload = base64.b64encode(b"hello\x00sandbox-value").decode()

    response = client.post(
        "/v1/run",
        json={
            "operation": "extract_strings",
            "payload_base64": payload,
            "min_length": 5,
            "max_results": 10,
        },
    )

    assert response.status_code == 200
    assert response.json()["strings"] == ["hello", "sandbox-value"]
    rejected = client.post("/v1/run", json={"operation": "shell", "command": "whoami"})
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_sandbox_runtime_never_falls_back_to_local_execution() -> None:
    runtime = SandboxRuntime("http://tool-sandbox.invalid")

    with pytest.raises(SandboxUnavailable, match="未在宿主机执行"):
        await runtime.execute(
            SandboxRequest(
                operation="extract_strings",
                payload_base64=base64.b64encode(b"local fallback must not run").decode(),
            )
        )


@pytest.mark.asyncio
async def test_sandbox_health_uses_internal_http_contract(monkeypatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://sandbox.test/health"
        return httpx.Response(200)

    class Client(httpx.AsyncClient):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("yuwang.tooling.runtime.sandbox.httpx.AsyncClient", Client)
    assert await SandboxRuntime("http://sandbox.test").health()
