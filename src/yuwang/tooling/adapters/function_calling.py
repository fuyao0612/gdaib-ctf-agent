"""OpenAI 兼容 Function Calling 与内部工具契约之间的无状态适配。"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from jsonschema import ValidationError as JsonSchemaValidationError  # type: ignore[import-untyped]
from jsonschema import validate as validate_json_schema
from pydantic import BaseModel, ConfigDict, Field

from yuwang.domain.models import ToolSnapshot


class ToolInvocation(BaseModel):
    """模型选择的工具调用；执行前仍须创建 ``ToolCallRequest`` 并经策略校验。"""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1, max_length=240)
    arguments: dict[str, Any]
    provider_tool_call_id: str = Field(min_length=1, max_length=200)


class NativeToolSelection(BaseModel):
    """原生响应可包含一次工具调用，或不调用工具时的受控 JSON 动作。"""

    model_config = ConfigDict(extra="forbid")

    invocation: ToolInvocation | None = None
    content: str | None = Field(default=None, max_length=100_000)


class FunctionCallingError(ValueError):
    """向用户暴露的协议错误不得包含原始响应或凭据。"""


def function_name(tool_id: str) -> str:
    """生成 OpenAI 兼容的稳定函数名，同时保留原始工具 ID 作为真实身份。"""

    value = tool_id.replace(".", "__")
    if len(value) > 64:
        raise FunctionCallingError("工具 ID 过长，无法转换为 OpenAI Function 名称")
    return value


@dataclass(frozen=True, slots=True)
class FunctionToolCatalog:
    tools: list[dict[str, Any]]
    by_function_name: dict[str, ToolSnapshot]

    @classmethod
    def from_snapshots(cls, snapshots: Iterable[ToolSnapshot]) -> FunctionToolCatalog:
        tools: list[dict[str, Any]] = []
        by_function_name: dict[str, ToolSnapshot] = {}
        for snapshot in snapshots:
            name = function_name(snapshot.tool_id)
            if name in by_function_name:
                raise FunctionCallingError("工具 Function 名称冲突")
            by_function_name[name] = snapshot
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"[{snapshot.tool_id}] {snapshot.description}",
                        "parameters": snapshot.input_schema,
                        "strict": True,
                    },
                }
            )
        return cls(tools=tools, by_function_name=by_function_name)

    def parse_response(self, body: Any) -> NativeToolSelection:
        """严格解析 ``tool_calls``，拒绝未知函数、非法 JSON 和越界参数。"""

        try:
            message = body["choices"][0]["message"]
            if message.get("refusal"):
                raise FunctionCallingError("模型出于安全策略拒绝工具调用请求")
            calls = message.get("tool_calls", [])
            if calls is None:
                calls = []
            if not isinstance(calls, list):
                raise TypeError("tool_calls 不是数组")
            if not calls:
                content = message.get("content")
                if content is not None and not isinstance(content, str):
                    raise TypeError("模型文本响应格式无效")
                return NativeToolSelection(content=content)
            if len(calls) != 1:
                raise FunctionCallingError("一次原生工具选择只能包含一个工具调用")
            call = calls[0]
            function = call["function"]
            function_name_value = function["name"]
            if not isinstance(function_name_value, str):
                raise TypeError("Function 名称无效")
            snapshot = self.by_function_name.get(function_name_value)
            if not snapshot:
                raise FunctionCallingError("模型请求了当前 Run 未启用的工具")
            raw_arguments = function["arguments"]
            if not isinstance(raw_arguments, str):
                raise TypeError("工具参数必须是 JSON 字符串")
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                raise TypeError("工具参数必须是 JSON 对象")
            validate_json_schema(instance=arguments, schema=snapshot.input_schema)
            provider_call_id = call["id"]
            if not isinstance(provider_call_id, str) or not provider_call_id:
                raise TypeError("tool_call_id 无效")
            return NativeToolSelection(
                invocation=ToolInvocation(
                    tool_id=snapshot.tool_id,
                    arguments=arguments,
                    provider_tool_call_id=provider_call_id,
                )
            )
        except FunctionCallingError:
            raise
        except (KeyError, TypeError, ValueError, JsonSchemaValidationError) as exc:
            raise FunctionCallingError("原生 Function Calling 响应不符合工具契约") from exc
