"""供 CI 和第三方工具测试复用的工具契约检查。"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema import validate as validate_json_schema
from pydantic import BaseModel, ConfigDict

from .contracts import ToolSpec
from .executor import ToolExecutor
from .plugin import ToolPlugin
from .registry import ToolRegistry

_PERMISSION_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*(?::[a-z][a-z0-9_-]*)*$")


def _validate_strict_object_schema(schema: object, location: str) -> None:
    if isinstance(schema, dict):
        is_object = schema.get("type") == "object" or "properties" in schema
        if is_object and schema.get("additionalProperties") is not False:
            raise ValueError(f"{location} 的对象 Schema 必须拒绝额外字段")
        for key, value in schema.items():
            _validate_strict_object_schema(value, f"{location}.{key}")
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            _validate_strict_object_schema(value, f"{location}[{index}]")


def validate_tool_spec_contract(spec: ToolSpec) -> None:
    """静态检查 ToolSpec、Schema 和权限声明，无需执行工具实现。"""

    payload = spec.model_dump(mode="json")
    json.dumps(payload, ensure_ascii=False, sort_keys=True)
    ToolSpec.model_validate(payload)
    for name, schema in (
        ("input_schema", spec.input_schema),
        ("output_schema", spec.output_schema),
        ("config_schema", spec.config_schema),
    ):
        Draft202012Validator.check_schema(schema)
        _validate_strict_object_schema(schema, name)
    if len(spec.permissions) != len(set(spec.permissions)):
        raise ValueError("工具权限不能重复")
    invalid_permissions = [
        permission for permission in spec.permissions if not _PERMISSION_PATTERN.fullmatch(permission)
    ]
    if invalid_permissions:
        raise ValueError("工具权限格式无效：" + ", ".join(invalid_permissions[:3]))
    if spec.requires_network and not any(permission.startswith("network:") for permission in spec.permissions):
        raise ValueError("需要网络的工具必须声明 network:* 权限")


def validate_registry_contracts(registry: ToolRegistry) -> list[str]:
    """检查当前注册表中所有启用工具，并返回稳定工具 ID 便于脚本输出。"""

    specs = list(registry.specs())
    ids = [spec.id for spec in specs]
    if len(ids) != len(set(ids)):
        raise ValueError("工具 ID 不能重复")
    for spec in specs:
        validate_tool_spec_contract(spec)
    return ids


async def assert_tool_execution_contract(
    tool: ToolPlugin[Any, Any], sample_input: dict[str, Any]
) -> None:
    """执行一组正反样例，验证输出 Schema 与额外字段拒绝真正经过执行边界。"""

    validate_tool_spec_contract(tool.spec)
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry)
    result = await executor.execute(tool.spec.id, sample_input)
    if not result.success:
        raise AssertionError(f"工具样例执行失败：{result.error}")
    validate_json_schema(instance=result.structured_output, schema=tool.spec.output_schema)
    extra_input = {**sample_input, "__contract_extra_field__": True}
    extra_result = await executor.execute(tool.spec.id, extra_input)
    if extra_result.success or not extra_result.error or extra_result.error.code != "invalid_input":
        raise AssertionError("工具输入没有拒绝额外字段")


class _BoundaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class _BoundaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class _DelayedContractTool(ToolPlugin[_BoundaryInput, _BoundaryOutput]):
    input_model = _BoundaryInput
    output_model = _BoundaryOutput

    @property
    def spec(self) -> ToolSpec:
        return _contract_spec("delayed_contract")

    async def execute(self, value: _BoundaryInput) -> _BoundaryOutput:
        await asyncio.sleep(0.05)
        return _BoundaryOutput(value=value.value)


class _FailingContractTool(ToolPlugin[_BoundaryInput, _BoundaryOutput]):
    input_model = _BoundaryInput
    output_model = _BoundaryOutput

    @property
    def spec(self) -> ToolSpec:
        return _contract_spec("failing_contract")

    async def execute(self, value: _BoundaryInput) -> _BoundaryOutput:
        del value
        raise RuntimeError("contract failure")


def _contract_spec(name: str) -> ToolSpec:
    return ToolSpec(
        namespace="contract",
        name=name,
        version="1.0.0",
        description="工具契约边界检查专用实现",
        capabilities=["contract"],
        scenarios=["test"],
        risk="low",
        permissions=[],
        requires_network=False,
        allowed_target_types=[],
        timeout_seconds=1,
        error_codes=["execution_error", "timeout"],
        idempotent=True,
        artifact_types=[],
        input_schema=_BoundaryInput.model_json_schema(),
        output_schema=_BoundaryOutput.model_json_schema(),
    )


async def assert_executor_boundary_contracts() -> None:
    """验证超时终止和实现异常隔离，不把异常传播给 API 或 Agent 主循环。"""

    registry = ToolRegistry()
    registry.register(_DelayedContractTool())
    registry.register(_FailingContractTool())
    executor = ToolExecutor(registry)
    timed_out = await executor.execute("contract.delayed_contract", {"value": "slow"}, 0.001)
    if timed_out.success or not timed_out.timed_out or timed_out.error is None:
        raise AssertionError("工具超时没有被执行器标记")
    failed = await executor.execute("contract.failing_contract", {"value": "failure"})
    if failed.success or failed.error is None or failed.error.code != "execution_error":
        raise AssertionError("工具实现异常没有被执行器隔离")


def validate_specs(specs: Iterable[ToolSpec]) -> None:
    """供无需注册表的工具清单复用静态检查。"""

    for spec in specs:
        validate_tool_spec_contract(spec)
