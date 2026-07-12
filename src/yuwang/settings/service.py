from __future__ import annotations

from typing import Protocol
from uuid import UUID

from yuwang.domain.models import utcnow
from yuwang.settings.models import (
    AgentDefaults,
    ProviderConfig,
    ProviderConfigInput,
    ProviderConfigView,
    validate_provider_url,
)
from yuwang.settings.security import SecretCipher


class SettingsRepository(Protocol):
    def list_provider_configs(self) -> list[ProviderConfig]: ...
    def get_provider_config(self, provider_id: UUID) -> ProviderConfig | None: ...
    def save_provider_config(self, value: ProviderConfig) -> ProviderConfig: ...
    def set_default_provider(self, provider_id: UUID) -> None: ...
    def delete_provider_config(self, provider_id: UUID) -> None: ...
    def get_agent_defaults(self) -> AgentDefaults: ...
    def save_agent_defaults(self, value: AgentDefaults) -> None: ...


class SettingsService:
    def __init__(
        self,
        repository: SettingsRepository,
        cipher: SecretCipher,
        *,
        allow_insecure_local: bool = False,
    ) -> None:
        self.repository = repository
        self.cipher = cipher
        self.allow_insecure_local = allow_insecure_local

    def list_providers(self, enabled_only: bool = False) -> list[ProviderConfigView]:
        values = self.repository.list_provider_configs()
        if enabled_only:
            values = [value for value in values if value.enabled]
        return [value.public_view() for value in values]

    def get_provider(self, provider_id: UUID) -> ProviderConfig:
        value = self.repository.get_provider_config(provider_id)
        if not value:
            raise KeyError("Provider 配置不存在")
        return value

    def create_provider(self, value: ProviderConfigInput) -> ProviderConfigView:
        if not value.api_key:
            raise ValueError("新建 Provider 必须填写 API Key")
        base_url = validate_provider_url(value.base_url, self.allow_insecure_local)
        config = ProviderConfig(
            name=value.name,
            preset=value.preset,
            base_url=base_url,
            model=value.model,
            encrypted_api_key=self.cipher.encrypt(value.api_key),
            enabled=value.enabled,
            is_default=value.is_default,
            fallback_order=value.fallback_order,
            timeout_seconds=value.timeout_seconds,
            max_retries=value.max_retries,
            structured_mode=value.structured_mode,
        )
        self.repository.save_provider_config(config)
        if config.is_default:
            self.repository.set_default_provider(config.id)
        return self.get_provider(config.id).public_view()

    def update_provider(self, provider_id: UUID, value: ProviderConfigInput) -> ProviderConfigView:
        current = self.get_provider(provider_id)
        current.name = value.name
        current.preset = value.preset
        current.base_url = validate_provider_url(value.base_url, self.allow_insecure_local)
        current.model = value.model
        if value.api_key:
            current.encrypted_api_key = self.cipher.encrypt(value.api_key)
        current.enabled = value.enabled
        current.is_default = value.is_default
        current.fallback_order = value.fallback_order
        current.timeout_seconds = value.timeout_seconds
        current.max_retries = value.max_retries
        current.structured_mode = value.structured_mode
        current.updated_at = utcnow().isoformat()
        self.repository.save_provider_config(current)
        if current.is_default:
            self.repository.set_default_provider(current.id)
        return self.get_provider(current.id).public_view()

    def delete_provider(self, provider_id: UUID) -> None:
        current = self.get_provider(provider_id)
        if current.is_default:
            raise ValueError("默认 Provider 不能删除，请先切换默认项")
        self.repository.delete_provider_config(provider_id)

    def decrypt_api_key(self, provider_id: UUID) -> str:
        return self.cipher.decrypt(self.get_provider(provider_id).encrypted_api_key)

    def resolve_chain(self, selected_id: UUID | None = None) -> list[ProviderConfig]:
        providers = [value for value in self.repository.list_provider_configs() if value.enabled]
        if selected_id:
            selected = next((value for value in providers if value.id == selected_id), None)
            if not selected:
                raise ValueError("所选 Provider 不存在或未启用")
            rest = [value for value in providers if value.id != selected_id]
            return [selected, *sorted(rest, key=self._fallback_key)]
        default = next((value for value in providers if value.is_default), None)
        if not default:
            raise ValueError("需要配置模型：请在设置中心启用并选择默认 Provider")
        rest = [value for value in providers if value.id != default.id]
        return [default, *sorted(rest, key=self._fallback_key)]

    @staticmethod
    def _fallback_key(value: ProviderConfig) -> tuple[int, str]:
        return (value.fallback_order if value.fallback_order is not None else 999, value.name)

    def get_agent_defaults(self) -> AgentDefaults:
        return self.repository.get_agent_defaults()

    def save_agent_defaults(self, value: AgentDefaults) -> AgentDefaults:
        self.repository.save_agent_defaults(value)
        return value
