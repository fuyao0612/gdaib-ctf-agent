"""设置用例服务：协调校验、密钥加密、默认项和 fallback 顺序。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from yuwang.domain.models import Run, Thread, utcnow
from yuwang.settings.models import (
    AgentDefaults,
    ChatDefaults,
    ProviderConfig,
    ProviderConfigInput,
    ProviderConfigView,
    resolve_structured_mode,
    validate_provider_url,
)
from yuwang.settings.profiles import AgentProfileVersion
from yuwang.settings.security import SecretCipher


class SettingsRepository(Protocol):
    def list_provider_configs(self) -> list[ProviderConfig]: ...
    def get_provider_config(self, provider_id: UUID) -> ProviderConfig | None: ...
    def save_provider_config(
        self, value: ProviderConfig, *, set_default: bool = False
    ) -> ProviderConfig: ...
    def set_default_provider(self, provider_id: UUID) -> None: ...
    def delete_provider_config(self, provider_id: UUID) -> None: ...
    def delete_provider_with_thread_fallback(
        self, provider_id: UUID, fallback_provider_id: UUID | None, notice: str
    ) -> int: ...
    def list_threads(self) -> list[Thread]: ...
    def list_active_runs_by_provider_id(self, provider_id: UUID) -> list[Run]: ...
    def list_agent_profiles(self) -> list[AgentProfileVersion]: ...
    def get_agent_defaults(self) -> AgentDefaults: ...
    def save_agent_defaults(self, value: AgentDefaults) -> None: ...
    def get_chat_defaults(self) -> ChatDefaults: ...
    def save_chat_defaults(self, value: ChatDefaults) -> None: ...


class SettingsService:
    """设置用例边界：负责密钥加密、URL 校验、默认项和 Provider 链解析。"""

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
        resolve_structured_mode(value.preset, value.structured_mode)
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
            input_price_per_million=value.input_price_per_million,
            output_price_per_million=value.output_price_per_million,
            structured_mode=value.structured_mode,
            fallback_on=value.fallback_on,
        )
        self.repository.save_provider_config(config, set_default=config.is_default)
        return self.get_provider(config.id).public_view()

    def update_provider(self, provider_id: UUID, value: ProviderConfigInput) -> ProviderConfigView:
        current = self.get_provider(provider_id)
        if current.is_default and not value.enabled:
            raise ValueError("默认 Provider 不能直接停用，请先切换默认项")
        if current.is_default and not value.is_default:
            raise ValueError("不能取消唯一默认 Provider，请先选择新的默认项")
        current.name = value.name
        resolve_structured_mode(value.preset, value.structured_mode)
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
        current.input_price_per_million = value.input_price_per_million
        current.output_price_per_million = value.output_price_per_million
        current.structured_mode = value.structured_mode
        current.tool_call_mode = value.tool_call_mode
        current.fallback_on = value.fallback_on
        current.updated_at = utcnow().isoformat()
        self.repository.save_provider_config(current, set_default=current.is_default)
        return self.get_provider(current.id).public_view()

    def provider_deletion_impact(self, provider_id: UUID) -> dict[str, object]:
        """返回可展示的删除影响，并把无法安全解除的引用变成明确阻断原因。"""

        current = self.get_provider(provider_id)
        fallback = self._fallback_default(excluding=provider_id)
        reasons: list[str] = []
        if current.is_default:
            reasons.append("该配置是全局默认 Provider，请先切换默认项")
        chat_defaults = self.get_chat_defaults()
        if chat_defaults.default_provider_id == provider_id:
            reasons.append("该配置是默认聊天模型，请先在聊天设置中切换默认项")
        profiles = [
            profile.name
            for profile in self.repository.list_agent_profiles()
            if profile.default_provider_id == provider_id
            or provider_id in profile.fallback_provider_ids
        ]
        if profiles:
            reasons.append(f"仍被 Agent 配置引用：{'、'.join(profiles)}")
        active_runs = self.repository.list_active_runs_by_provider_id(provider_id)
        if active_runs:
            reasons.append(f"仍被 {len(active_runs)} 个活动 Run 使用")
        threads = [
            thread
            for thread in self.repository.list_threads()
            if thread.provider_config_id == provider_id
        ]
        if threads and not fallback:
            reasons.append("没有可用的全局默认 Provider，无法安全回退会话选择")
        return {
            "id": str(current.id),
            "name": current.name,
            "model": current.model,
            "affected_thread_count": len(threads),
            "fallback_provider": (
                {"id": str(fallback.id), "name": fallback.name, "model": fallback.model}
                if fallback
                else None
            ),
            "blocking_reasons": reasons,
        }

    def delete_provider(self, provider_id: UUID) -> None:
        current = self.get_provider(provider_id)
        impact = self.provider_deletion_impact(provider_id)
        raw_reasons = impact["blocking_reasons"]
        reasons = [str(value) for value in raw_reasons] if isinstance(raw_reasons, list) else []
        if reasons:
            raise ValueError(f"无法删除 Provider：{'；'.join(reasons)}")
        fallback = self._fallback_default(excluding=provider_id)
        notice = (
            f"已删除原选择的 Provider“{current.name} · {current.model}”，"
            "会话已回退到全局默认模型。"
        )
        self.repository.delete_provider_with_thread_fallback(
            provider_id, fallback.id if fallback else None, notice
        )

    def _fallback_default(self, *, excluding: UUID | None = None) -> ProviderConfig | None:
        """只使用已启用的全局默认 Provider 作为会话选择的安全回退。"""

        providers = [
            value
            for value in self.repository.list_provider_configs()
            if value.enabled and value.id != excluding
        ]
        return next((value for value in providers if value.is_default), None)

    def decrypt_api_key(self, provider_id: UUID) -> str:
        return self.cipher.decrypt(self.get_provider(provider_id).encrypted_api_key)

    def record_connection_test(
        self,
        provider_id: UUID,
        *,
        succeeded: bool,
        actual_model: str | None = None,
        error: str | None = None,
    ) -> ProviderConfigView:
        """保存最近一次真实连接测试，供设置页和就绪探针共同判断。"""
        current = self.get_provider(provider_id)
        current.connection_status = "ok" if succeeded else "failed"
        current.last_tested_at = utcnow().isoformat()
        current.last_test_error = None if succeeded else (error or "连接测试失败")[:500]
        current.actual_model = actual_model if succeeded else None
        current.updated_at = utcnow().isoformat()
        self.repository.save_provider_config(current)
        return current.public_view()

    def resolve_chain(
        self, selected_id: UUID | None = None, fallback_ids: list[UUID] | None = None
    ) -> list[ProviderConfig]:
        providers = [value for value in self.repository.list_provider_configs() if value.enabled]
        if selected_id:
            selected = next((value for value in providers if value.id == selected_id), None)
            if not selected:
                raise ValueError("所选 Provider 不存在或未启用")
            rest = [value for value in providers if value.id != selected_id]
            if fallback_ids is not None:
                positions = {value: index for index, value in enumerate(fallback_ids)}
                rest = [value for value in rest if value.id in positions]
                rest.sort(key=lambda value: positions[value.id])
                return [selected, *rest]
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

    def get_chat_defaults(self) -> ChatDefaults:
        return self.repository.get_chat_defaults()

    def save_chat_defaults(self, value: ChatDefaults) -> ChatDefaults:
        self.repository.save_chat_defaults(value)
        return value
