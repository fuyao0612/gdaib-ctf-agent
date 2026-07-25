"""统一工具协议的独立契约测试。"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from tests.fakes import FakeEchoTool
from yuwang.tooling import (
    ToolCallRequest,
    ToolExecutor,
    ToolPlugin,
    ToolRegistry,
    ToolSpec,
    assert_executor_boundary_contracts,
    assert_tool_execution_contract,
)


class ProgressInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class ProgressOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    echoed: str


class ProgressTool(ToolPlugin[ProgressInput, ProgressOutput]):
    input_model = ProgressInput
    output_model = ProgressOutput

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            namespace="test",
            name="progress",
            version="1.0.0",
            description="用于验证进度与工具契约的测试工具",
            capabilities=["test"],
            scenarios=["test"],
            risk="low",
            permissions=[],
            requires_network=False,
            allowed_target_types=[],
            timeout_seconds=1,
            error_codes=[],
            idempotent=True,
            artifact_types=[],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute(self, value: ProgressInput) -> ProgressOutput:
        await self.report_progress(50, "正在处理测试输入")
        return ProgressOutput(echoed=value.text)


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


@pytest.mark.asyncio
async def test_tool_contract_checker_executes_schema_and_executor_boundaries() -> None:
    await assert_tool_execution_contract(ProgressTool(), {"text": "verified"})
    await assert_executor_boundary_contracts()


@pytest.mark.asyncio
async def test_executor_reports_structured_progress_for_current_call_only() -> None:
    registry = ToolRegistry()
    registry.register(ProgressTool())
    tool = registry.get("test.progress")
    request = ToolCallRequest(
        tool_id=tool.spec.id,
        tool_version=tool.spec.version,
        arguments={"text": "verified"},
    )
    reported = []

    async def capture(progress):
        reported.append(progress)

    result = await ToolExecutor(registry).execute_call(request, progress_reporter=capture)

    assert result.success
    assert [(item.call_id, item.percent, item.message) for item in reported] == [
        (request.call_id, 50, "正在处理测试输入")
    ]
