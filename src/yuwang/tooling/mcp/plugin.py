"""将受控 MCP Tool 映射为现有 ToolPlugin，而不是绕过工具注册与执行器。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from yuwang.tooling.contracts import ToolSpec
from yuwang.tooling.plugin import ToolPlugin


class McpArguments(BaseModel):
    """实际字段由 ToolSpec.input_schema 在执行器层严格验证。"""

    model_config = ConfigDict(extra="allow")


class McpOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # MCP 返回值属于不可信外部数据，平台只严格约束外层信封，保留内部原始形状。
    content: list[Any] = Field(default_factory=list)
    structured_content: Any = None
    is_error: bool = False


McpCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class McpToolPlugin(ToolPlugin[McpArguments, McpOutput]):
    input_model = McpArguments
    output_model = McpOutput

    def __init__(
        self,
        *,
        server_id: str,
        server_name: str,
        tool: dict[str, Any],
        call: McpCall,
    ) -> None:
        name = tool.get("name")
        schema = tool.get("inputSchema")
        if not isinstance(name, str) or not name:
            raise ValueError("MCP tools/list 返回的工具名称无效")
        if not isinstance(schema, dict):
            raise ValueError("MCP tools/list 返回的输入 Schema 无效")
        self.server_id = server_id
        self.server_name = server_name
        self.tool_name = name
        self.input_schema = schema
        self.description = str(tool.get("description") or "MCP 工具")[:2_000]
        self.call = call

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            namespace=f"mcp.{self.server_id}",
            name=self.tool_name,
            version="1.0.0",
            author=self.server_name,
            source=f"mcp:{self.server_id}",
            source_type="mcp",
            description=self.description,
            capabilities=["mcp"],
            scenarios=["mcp"],
            risk="medium",
            permissions=["mcp:call"],
            requires_network=False,
            allowed_target_types=[],
            timeout_seconds=30,
            error_codes=["mcp_connection_failed", "mcp_tool_error"],
            idempotent=False,
            artifact_types=[],
            input_schema=self.input_schema,
            output_schema=self.output_model.model_json_schema(),
            supports_cancellation=False,
            supports_progress=False,
        )

    async def execute(self, value: McpArguments) -> McpOutput:
        result = await self.call(self.tool_name, value.model_dump(mode="json"))
        return McpOutput.model_validate(result)
