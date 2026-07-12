from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yuwang.domain.models import Budget, utcnow


class ProviderPreset(StrEnum):
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    GLM = "glm"
    CUSTOM = "custom"


StructuredMode = Literal["auto", "json_schema", "json_object", "prompt_json"]
FallbackCategory = Literal["rate_limit", "timeout", "invalid_output", "service"]
DEFAULT_FALLBACK_CATEGORIES: list[FallbackCategory] = ["rate_limit", "timeout", "service"]


PROVIDER_PRESETS: dict[ProviderPreset, dict[str, Any]] = {
    ProviderPreset.DEEPSEEK: {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "structured_modes": ["json_object", "prompt_json"],
        "preferred_structured_mode": "json_object",
        "supports_model_discovery": True,
    },
    ProviderPreset.QWEN: {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.7-plus",
        "models": ["qwen3.7-plus", "qwen3.6-plus", "qwen-flash"],
        "structured_modes": ["json_object", "prompt_json"],
        "preferred_structured_mode": "json_object",
        "supports_model_discovery": True,
    },
    ProviderPreset.GLM: {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-5.1",
        "models": ["glm-5.1", "glm-5", "glm-4.7"],
        "structured_modes": ["json_object", "prompt_json"],
        "preferred_structured_mode": "json_object",
        "supports_model_discovery": True,
    },
    ProviderPreset.CUSTOM: {
        "base_url": "https://provider.example/v1",
        "model": "model-name",
        "models": [],
        "structured_modes": ["json_schema", "json_object", "prompt_json"],
        "preferred_structured_mode": "json_object",
        "supports_model_discovery": True,
    },
}


def resolve_structured_mode(preset: ProviderPreset, requested: StructuredMode) -> str:
    descriptor = PROVIDER_PRESETS[preset]
    if requested == "auto":
        return str(descriptor["preferred_structured_mode"])
    # v0.2 saved every preset as json_schema. Negotiate those historical rows to
    # the documented vendor mode instead of breaking startup or run recovery.
    if requested == "json_schema" and preset != ProviderPreset.CUSTOM:
        return str(descriptor["preferred_structured_mode"])
    if requested not in descriptor["structured_modes"]:
        supported = "、".join(descriptor["structured_modes"])
        raise ValueError(f"该 Provider 不支持 {requested}，可选模式：{supported}")
    return requested


def validate_provider_url(value: str, allow_insecure_local: bool = False) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password:
        raise ValueError("Base URL 禁止内嵌凭据")
    if parsed.query or parsed.fragment:
        raise ValueError("Base URL 禁止查询参数或片段")
    if not parsed.hostname:
        raise ValueError("Base URL 缺少主机名")
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (
        allow_insecure_local and local and parsed.scheme == "http"
    ):
        raise ValueError("Base URL 必须使用 HTTPS")
    return value.rstrip("/")


class ProviderConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    preset: ProviderPreset
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=160)
    api_key: str | None = Field(default=None, min_length=8, max_length=4096)
    enabled: bool = True
    is_default: bool = False
    fallback_order: int | None = Field(default=None, ge=0, le=100)
    timeout_seconds: float = Field(default=60, ge=1, le=600)
    max_retries: int = Field(default=2, ge=0, le=8)
    input_price_per_million: float = Field(default=0, ge=0, le=1_000_000)
    output_price_per_million: float = Field(default=0, ge=0, le=1_000_000)
    structured_mode: StructuredMode = "auto"
    fallback_on: list[FallbackCategory] = Field(
        default_factory=lambda: list(DEFAULT_FALLBACK_CATEGORIES), max_length=4
    )

    @field_validator("api_key")
    @classmethod
    def strip_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("API Key 不能为空")
        return stripped


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    name: str
    preset: ProviderPreset
    base_url: str
    model: str
    encrypted_api_key: str
    enabled: bool
    is_default: bool
    fallback_order: int | None
    timeout_seconds: float
    max_retries: int
    input_price_per_million: float = 0
    output_price_per_million: float = 0
    structured_mode: StructuredMode = "auto"
    fallback_on: list[FallbackCategory] = Field(
        default_factory=lambda: list(DEFAULT_FALLBACK_CATEGORIES)
    )
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: utcnow().isoformat())

    def public_view(self) -> ProviderConfigView:
        return ProviderConfigView(
            id=self.id,
            name=self.name,
            preset=self.preset,
            base_url=self.base_url,
            model=self.model,
            enabled=self.enabled,
            is_default=self.is_default,
            fallback_order=self.fallback_order,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            input_price_per_million=self.input_price_per_million,
            output_price_per_million=self.output_price_per_million,
            structured_mode=self.structured_mode,
            resolved_structured_mode=resolve_structured_mode(self.preset, self.structured_mode),
            fallback_on=self.fallback_on,
            has_api_key=bool(self.encrypted_api_key),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class ProviderConfigView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: str
    preset: ProviderPreset
    base_url: str
    model: str
    enabled: bool
    is_default: bool
    fallback_order: int | None
    timeout_seconds: float
    max_retries: int
    input_price_per_million: float
    output_price_per_million: float
    structured_mode: StructuredMode
    resolved_structured_mode: str
    fallback_on: list[FallbackCategory]
    has_api_key: bool
    created_at: str
    updated_at: str


class AgentDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budget: Budget = Field(default_factory=Budget)
    provider_retry_budget: int = Field(default=2, ge=0, le=10)
    context_token_budget: int = Field(default=32000, ge=1024, le=2_000_000)
    observation_char_budget: int = Field(default=20000, ge=1000, le=1_000_000)
