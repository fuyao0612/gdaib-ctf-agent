from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yuwang.domain.models import Budget, utcnow


class ProviderPreset(StrEnum):
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    GLM = "glm"
    CUSTOM = "custom"


PROVIDER_PRESETS: dict[ProviderPreset, dict[str, str]] = {
    ProviderPreset.DEEPSEEK: {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
    },
    ProviderPreset.QWEN: {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    ProviderPreset.GLM: {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.5-flash",
    },
}


def validate_provider_url(value: str, allow_insecure_local: bool = False) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password:
        raise ValueError("Base URL 禁止内嵌凭据")
    if parsed.query or parsed.fragment:
        raise ValueError("Base URL 禁止查询参数或片段")
    if not parsed.hostname:
        raise ValueError("Base URL 缺少主机名")
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (allow_insecure_local and local and parsed.scheme == "http"):
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
    structured_mode: str = Field(default="json_schema", pattern="^(json_schema|json_object)$")

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
    structured_mode: str
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
            structured_mode=self.structured_mode,
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
    structured_mode: str
    has_api_key: bool
    created_at: str
    updated_at: str


class AgentDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budget: Budget = Field(default_factory=Budget)
    provider_retry_budget: int = Field(default=2, ge=0, le=10)
    context_token_budget: int = Field(default=32000, ge=1024, le=2_000_000)
    observation_char_budget: int = Field(default=20000, ge=1000, le=1_000_000)
