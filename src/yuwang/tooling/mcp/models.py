"""MCP 服务配置模型；公开视图绝不返回认证明文。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yuwang.domain.models import utcnow

McpTransport = Literal["stdio", "streamable_http"]
McpHealthStatus = Literal["healthy", "degraded", "unavailable", "disabled", "untested"]


class McpServerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    transport: McpTransport
    command: str | None = Field(default=None, min_length=1, max_length=500)
    args: list[str] = Field(default_factory=list, max_length=40)
    url: str | None = Field(default=None, min_length=1, max_length=1_000)
    auth_token: str | None = Field(default=None, min_length=1, max_length=4_096)
    enabled: bool = True
    connect_timeout_seconds: float = Field(default=10, gt=0, le=120)
    call_timeout_seconds: float = Field(default=30, gt=0, le=300)
    allowed_tools: list[str] = Field(default_factory=list, max_length=200)
    blocked_tools: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_transport_fields(self) -> McpServerInput:
        if self.transport == "stdio":
            if not self.command or self.url:
                raise ValueError("stdio MCP 必须提供 command，且不能提供 url")
        elif not self.url or self.command:
            raise ValueError("Streamable HTTP MCP 必须提供 url，且不能提供 command")
        overlap = set(self.allowed_tools) & set(self.blocked_tools)
        if overlap:
            raise ValueError("allowed_tools 和 blocked_tools 不能包含同一工具")
        return self


class McpServerConfig(McpServerInput):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    encrypted_auth_token: str = ""
    health_status: McpHealthStatus = "untested"
    last_connected_at: str | None = None
    last_error: str | None = Field(default=None, max_length=500)
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: utcnow().isoformat())

    def public_view(self) -> McpServerView:
        return McpServerView(
            id=self.id,
            name=self.name,
            transport=self.transport,
            command=self.command,
            args=self.args,
            url=self.url,
            has_auth=bool(self.encrypted_auth_token),
            enabled=self.enabled,
            connect_timeout_seconds=self.connect_timeout_seconds,
            call_timeout_seconds=self.call_timeout_seconds,
            allowed_tools=self.allowed_tools,
            blocked_tools=self.blocked_tools,
            health_status=self.health_status,
            last_connected_at=self.last_connected_at,
            last_error=self.last_error,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class McpServerView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    transport: McpTransport
    command: str | None
    args: list[str]
    url: str | None
    has_auth: bool
    enabled: bool
    connect_timeout_seconds: float
    call_timeout_seconds: float
    allowed_tools: list[str]
    blocked_tools: list[str]
    health_status: McpHealthStatus
    last_connected_at: str | None
    last_error: str | None
    created_at: str
    updated_at: str
