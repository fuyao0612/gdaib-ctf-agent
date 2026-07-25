"""统一工具协议与调用契约。

该模块只描述跨运行时共享的数据，不依赖 Agent、FastAPI 或具体工具实现。这样
本地插件、MCP 和后续沙箱都可以落到同一个请求与结果模型中。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _strict_object_schemas(value: Any) -> Any:
    """为 Pydantic 默认生成的对象 Schema 补上额外字段拒绝规则。"""

    if isinstance(value, dict):
        copied = {key: _strict_object_schemas(item) for key, item in value.items()}
        if copied.get("type") == "object" or "properties" in copied:
            copied["additionalProperties"] = False
        return copied
    if isinstance(value, list):
        return [_strict_object_schemas(item) for item in value]
    return value


class ToolHealth(BaseModel):
    """工具当前健康状态；错误文本必须已经在边界处脱敏。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy", "degraded", "unavailable", "disabled"] = "healthy"
    checked_at: datetime = Field(default_factory=_utcnow)
    last_error: str | None = Field(default=None, max_length=500)


class ToolSpec(BaseModel):
    """所有工具来源共用的静态元数据。

    新字段均有兼容默认值，旧的 ``ToolPlugin`` 可以不改签名继续注册；注册表会
    根据 namespace 与 name 固化稳定 ``id``。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    namespace: str = "builtin"
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    display_name: str | None = Field(default=None, max_length=160)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
    author: str = Field(default="御网智元", min_length=1, max_length=120)
    source: str = Field(default="builtin", min_length=1, max_length=300)
    source_type: Literal["builtin", "python_plugin", "mcp"] = "builtin"
    description: str = Field(min_length=1, max_length=2_000)
    capabilities: list[str] = Field(default_factory=list, max_length=50)
    scenarios: list[str] = Field(default_factory=list, max_length=50)
    # 只接受三档固定风险。高风险工具即使未来注册也会由策略默认拒绝。
    risk: Literal["low", "medium", "high"]
    permissions: list[str] = Field(default_factory=list, max_length=50)
    requires_network: bool
    allowed_target_types: list[str] = Field(default_factory=list, max_length=30)
    timeout_seconds: float = Field(gt=0, le=120)
    error_codes: list[str] = Field(default_factory=list, max_length=50)
    idempotent: bool
    artifact_types: list[str] = Field(default_factory=list, max_length=30)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    config_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    min_platform_version: str = "0.5.0"
    max_platform_version: str | None = None
    supports_cancellation: bool = False
    supports_progress: bool = False
    enabled: bool = True
    health: ToolHealth = Field(default_factory=ToolHealth)

    @model_validator(mode="after")
    def normalize_contract(self) -> ToolSpec:
        normalized_namespace = self.namespace.strip(".")
        if not normalized_namespace:
            raise ValueError("工具命名空间不能为空")
        self.namespace = normalized_namespace
        expected_id = f"{self.namespace}.{self.name}"
        if self.id and self.id != expected_id:
            raise ValueError("工具 ID 必须与命名空间和名称一致")
        self.id = expected_id
        self.display_name = self.display_name or self.name
        self.input_schema = _strict_object_schemas(self.input_schema)
        self.output_schema = _strict_object_schemas(self.output_schema)
        self.config_schema = _strict_object_schemas(self.config_schema)
        return self


class ToolCallRequest(BaseModel):
    """唯一内部工具调用请求；所有外部协议先转换为它。"""

    model_config = ConfigDict(extra="forbid")

    call_id: UUID = Field(default_factory=uuid4)
    run_id: UUID | None = None
    tool_id: str = Field(min_length=1, max_length=240)
    tool_version: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)
    target_scope: list[str] = Field(default_factory=list, max_length=100)
    approval_fingerprint: str | None = Field(default=None, max_length=128)
    requested_at: datetime = Field(default_factory=_utcnow)


class ToolCallError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False
    security_related: bool = False


class ToolProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: UUID
    percent: float = Field(ge=0, le=100)
    message: str = Field(min_length=1, max_length=500)
    reported_at: datetime = Field(default_factory=_utcnow)


class ToolCallResult(BaseModel):
    """统一的工具终态；``output`` 属性为旧 Agent 代码保留。"""

    model_config = ConfigDict(extra="forbid")

    call_id: UUID = Field(default_factory=uuid4)
    success: bool
    status: Literal["succeeded", "failed", "cancelled", "timed_out"]
    summary: str = Field(min_length=1, max_length=2_000)
    structured_output: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    error: ToolCallError | None = None
    duration_ms: int = Field(default=0, ge=0)
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime = Field(default_factory=_utcnow)
    cancelled: bool = False
    timed_out: bool = False
    executed_tool_id: str = Field(min_length=1, max_length=240)
    executed_tool_version: str = Field(min_length=1, max_length=80)

    @property
    def output(self) -> dict[str, Any]:
        """兼容原有 ``ToolResult.output`` 读取方式。"""

        return self.structured_output


# 旧 SDK 对外名称仍有效，避免第三方插件被一次重构打断。
ToolError = ToolCallError
ToolResult = ToolCallResult
