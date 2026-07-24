"""统一工具协议的独立契约测试。"""

from __future__ import annotations

import pytest

from tests.fakes import FakeEchoTool
from yuwang.tooling import ToolCallRequest, ToolExecutor, ToolRegistry


@pytest.mark.asyncio
async def test_executor_rejects_extra_fields_from_normalized_schema() -> None:
    """即使旧插件的 Pydantic 模型未声明 forbid，执行边界也必须拒绝额外字段。"""

    registry = ToolRegistry()
    registry.register(FakeEchoTool())

    result = await ToolExecutor(registry).execute(
        "test_echo", {"text": "ok", "unexpected": "reject"}
    )

    assert not result.success
    assert result.error and result.error.code == "invalid_input"


@pytest.mark.asyncio
async def test_execute_call_preserves_request_identity_and_version() -> None:
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    tool = registry.get("test_echo")
    request = ToolCallRequest(
        tool_id=tool.spec.id,
        tool_version=tool.spec.version,
        arguments={"text": "verified"},
    )

    result = await ToolExecutor(registry).execute_call(request)

    assert result.success
    assert result.call_id == request.call_id
    assert result.executed_tool_id == "builtin.test_echo"
    assert result.executed_tool_version == "1.0.0"
    assert result.output == {"echoed": "verified"}


@pytest.mark.asyncio
async def test_execute_call_rejects_a_changed_tool_version() -> None:
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    request = ToolCallRequest(
        tool_id="builtin.test_echo",
        tool_version="9.9.9",
        arguments={"text": "verified"},
    )

    result = await ToolExecutor(registry).execute_call(request)

    assert not result.success
    assert result.error and result.error.code == "execution_error"
    assert "版本" in result.error.message
