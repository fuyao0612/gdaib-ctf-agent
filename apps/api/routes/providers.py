"""Provider、模型发现与平台默认设置路由。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from apps.api.context import ApiContext
from yuwang.model_providers import OpenAICompatibleProvider, ProviderError
from yuwang.settings import AgentDefaults, ProviderConfigInput, ProviderConfigView
from yuwang.settings.models import PROVIDER_PRESETS, ProviderPreset, resolve_structured_mode


def create_provider_router(context: ApiContext) -> APIRouter:
    """创建公开 Provider 摘要和受保护的完整配置路由。"""

    router = APIRouter(prefix="/api/v1", tags=["providers"])

    @router.get("/providers")
    async def providers() -> list[ProviderConfigView]:
        if not context.config.master_key:
            return []
        return context.get_settings_service().list_providers(enabled_only=True)

    @router.get("/provider-presets")
    async def provider_presets() -> dict[str, dict[str, Any]]:
        return {key.value: value for key, value in PROVIDER_PRESETS.items()}

    admin_prefix = "/admin/settings/providers"

    @router.get(
        admin_prefix,
        response_model=list[ProviderConfigView],
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_list_providers() -> list[ProviderConfigView]:
        return context.get_settings_service().list_providers()

    @router.post(
        admin_prefix,
        response_model=ProviderConfigView,
        status_code=201,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_create_provider(body: ProviderConfigInput) -> ProviderConfigView:
        try:
            return context.get_settings_service().create_provider(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.put(
        f"{admin_prefix}/{{provider_id}}",
        response_model=ProviderConfigView,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_update_provider(
        provider_id: UUID,
        body: ProviderConfigInput,
    ) -> ProviderConfigView:
        try:
            return context.get_settings_service().update_provider(provider_id, body)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get(
        f"{admin_prefix}/{{provider_id}}/deletion-impact",
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_provider_deletion_impact(provider_id: UUID) -> dict[str, object]:
        try:
            return context.get_settings_service().provider_deletion_impact(provider_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.delete(
        f"{admin_prefix}/{{provider_id}}",
        status_code=204,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_delete_provider(provider_id: UUID) -> None:
        try:
            context.get_settings_service().delete_provider(provider_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post(
        f"{admin_prefix}/{{provider_id}}/test",
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_test_provider(provider_id: UUID) -> dict[str, Any]:
        service = context.get_settings_service()
        try:
            value = service.get_provider(provider_id)
            provider = OpenAICompatibleProvider(
                name=value.name,
                base_url=value.base_url,
                api_key=service.decrypt_api_key(value.id),
                model=value.model,
                timeout_seconds=value.timeout_seconds,
                max_retries=value.max_retries,
                structured_mode=resolve_structured_mode(value.preset, value.structured_mode),
                fallback_on=value.fallback_on,
                input_price_per_million=value.input_price_per_million,
                output_price_per_million=value.output_price_per_million,
                request_overrides=(
                    {"enable_thinking": False} if value.preset == ProviderPreset.QWEN else {}
                ),
            )
            metrics = await provider.test_connection()
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ProviderError as exc:
            service.record_connection_test(provider_id, succeeded=False, error=str(exc))
            raise HTTPException(502, f"连接测试失败：{exc}") from exc
        service.record_connection_test(
            provider_id,
            succeeded=True,
            actual_model=metrics.model,
        )
        return {
            "status": "ok",
            "provider": metrics.provider,
            "model": metrics.model,
            "structured_mode": provider.structured_mode,
            "latency_ms": metrics.duration_ms,
            "usage_reported": metrics.usage_reported,
        }

    @router.get(
        f"{admin_prefix}/{{provider_id}}/models",
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_discover_provider_models(provider_id: UUID) -> dict[str, Any]:
        service = context.get_settings_service()
        try:
            value = service.get_provider(provider_id)
            provider = OpenAICompatibleProvider(
                name=value.name,
                base_url=value.base_url,
                api_key=service.decrypt_api_key(value.id),
                model=value.model,
                timeout_seconds=value.timeout_seconds,
                max_retries=0,
                structured_mode=resolve_structured_mode(value.preset, value.structured_mode),
            )
            models = await provider.discover_models()
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(502, f"模型发现失败：{exc}") from exc
        return {"models": models, "manual_model_supported": True}

    @router.get(
        "/admin/settings/agent",
        response_model=AgentDefaults,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_get_agent_defaults() -> AgentDefaults:
        return context.get_settings_service().get_agent_defaults()

    @router.put(
        "/admin/settings/agent",
        response_model=AgentDefaults,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_update_agent_defaults(body: AgentDefaults) -> AgentDefaults:
        return context.get_settings_service().save_agent_defaults(body)

    return router
