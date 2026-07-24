"""真实官方 MCP SDK 的 stdio 集成与安全配置测试。"""

from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from yuwang.domain.models import Run, TaskSpec, ToolSnapshot
from yuwang.settings import SecretCipher
from yuwang.storage import SQLiteRepository
from yuwang.tooling import ToolExecutor, ToolRegistry
from yuwang.tooling.mcp import McpServerInput, McpService
from yuwang.tooling.mcp.client import McpClient
from yuwang.tooling.mcp.models import McpServerConfig
from yuwang.tooling.mcp.security import assert_resolved_endpoint_is_safe


def allowed_python() -> set[str]:
    return {str(Path(sys.executable).resolve()).casefold()}


def unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as value:
        value.bind(("127.0.0.1", 0))
        return int(value.getsockname()[1])


@pytest.mark.asyncio
async def test_stdio_mcp_discovers_and_executes_through_the_tool_registry(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "mcp.db")
    registry = ToolRegistry()
    service = McpService(
        repository,
        SecretCipher(Fernet.generate_key().decode()),
        McpClient(allowed_commands=allowed_python()),
    )
    view = service.create(
        McpServerInput(
            name="stdio 测试服务",
            transport="stdio",
            command=sys.executable,
            args=["-m", "tests.mcp_test_server"],
            auth_token="mcp-test-token",
        )
    )

    discovered = await service.refresh(view.id, registry)
    tool_id = f"mcp.{view.id}.echo"
    result = await ToolExecutor(registry).execute(tool_id, {"text": "hello mcp"})

    assert [item["id"] for item in discovered] == [tool_id]
    assert result.success, result.error
    assert result.output["is_error"] is False
    assert "mcp-test-token" not in (tmp_path / "mcp.db").read_text(
        encoding="utf-8", errors="ignore"
    )
    stored = repository.get_mcp_server(view.id)
    assert stored and stored.health_status == "healthy" and stored.encrypted_auth_token


@pytest.mark.asyncio
async def test_streamable_http_mcp_discovers_and_executes(tmp_path) -> None:
    port = unused_local_port()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "tests.mcp_http_server:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    repository = SQLiteRepository(tmp_path / "mcp-http.db")
    registry = ToolRegistry()
    service = McpService(
        repository,
        SecretCipher(Fernet.generate_key().decode()),
        McpClient(allowed_commands=set(), allow_insecure_local=True),
    )
    try:
        view = service.create(
            McpServerInput(
                name="HTTP 测试服务",
                transport="streamable_http",
                url=f"http://127.0.0.1:{port}/mcp",
                allowed_tools=["echo"],
            )
        )
        for _ in range(20):
            try:
                discovered = await service.refresh(view.id, registry)
                break
            except ValueError:
                await asyncio.sleep(0.1)
        else:
            pytest.fail("Streamable HTTP MCP 未在测试时限内启动")
        tool_id = f"mcp.{view.id}.echo"
        result = await ToolExecutor(registry).execute(tool_id, {"text": "http mcp"})
        assert [item["id"] for item in discovered] == [tool_id]
        assert result.success, result.error
    finally:
        process.terminate()
        await process.wait()


def test_mcp_rejects_shell_programs_and_protected_http_targets(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "mcp.db")
    service = McpService(
        repository,
        SecretCipher(Fernet.generate_key().decode()),
        McpClient(allowed_commands={str(Path("pwsh").resolve()).casefold()}),
    )

    with pytest.raises(ValueError, match="Shell"):
        service.create(
            McpServerInput(name="禁止 Shell", transport="stdio", command="pwsh", args=["-c", "echo"])
        )
    with pytest.raises(ValueError, match="受保护网络"):
        assert_resolved_endpoint_is_safe("https://169.254.169.254/mcp", allow_insecure_local=False)


def test_mcp_config_model_requires_separated_transport_fields() -> None:
    with pytest.raises(ValueError, match="必须提供 command"):
        McpServerConfig(name="bad", transport="stdio")
    with pytest.raises(ValueError, match="必须提供 url"):
        McpServerConfig(name="bad", transport="streamable_http")


def test_mcp_deletion_impact_blocks_active_snapshot_reference(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "mcp.db")
    service = McpService(
        repository,
        SecretCipher(Fernet.generate_key().decode()),
        McpClient(allowed_commands=set(), allow_insecure_local=True),
    )
    created = service.create(
        McpServerInput(
            name="待检查 MCP",
            transport="streamable_http",
            url="http://127.0.0.1:9876/mcp",
        )
    )

    snapshot = ToolSnapshot(
        tool_id=f"mcp.{created.id}.echo",
        namespace=f"mcp.{created.id}",
        name="echo",
        display_name="echo",
        version="1.0.0",
        source_type="mcp",
        source=f"mcp:{created.id}",
        description="测试 MCP 工具",
        capabilities=["mcp"],
        scenarios=["mcp"],
        risk="medium",
        permissions=["mcp:call"],
        requires_network=False,
        allowed_target_types=[],
        timeout_seconds=30,
        error_codes=[],
        idempotent=False,
        artifact_types=[],
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object", "additionalProperties": False},
    )
    run = repository.save_run(Run(thread_id=uuid4()))
    repository.save_run_task(run.id, TaskSpec(body="检查 MCP 引用", tool_snapshots=[snapshot]))

    impact = service.deletion_impact(created.id)

    assert impact.active_run_count == 1
    assert impact.historical_snapshot_count == 1
    assert impact.blocking_reasons
    with pytest.raises(ValueError, match="运行中的任务"):
        service.delete(created.id, ToolRegistry())
