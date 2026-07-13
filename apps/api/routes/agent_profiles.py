"""公开 Agent 列表与管理员版本管理路由。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from apps.api.context import ApiContext
from apps.api.schemas import AgentProfileSummary, ProfileCopy, TemplatePreview
from yuwang.settings import (
    AgentProfileExport,
    AgentProfileInput,
    AgentProfileVersion,
    SafeTemplateRenderer,
)


def create_agent_profile_router(context: ApiContext) -> APIRouter:
    """创建 AgentProfile 的公开读取和受保护管理路由。"""

    router = APIRouter(prefix="/api/v1", tags=["agent-profiles"])
    service = context.profile_service
    repository = context.repository

    @router.get("/agent-profiles", response_model=list[AgentProfileSummary])
    async def public_agent_profiles() -> list[AgentProfileSummary]:
        return [
            AgentProfileSummary(
                profile_id=value.profile_id,
                version=value.version,
                name=value.name,
                description=value.description,
                run_mode=value.run_mode,
                completion_mode=value.completion_mode,
                is_default=value.is_default,
            )
            for value in repository.list_agent_profiles()
            if value.enabled
        ]

    admin_prefix = "/admin/settings/agent-profiles"

    @router.get(
        admin_prefix,
        response_model=list[AgentProfileVersion],
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_list_agent_profiles() -> list[AgentProfileVersion]:
        return repository.list_agent_profiles()

    @router.post(
        admin_prefix,
        response_model=AgentProfileVersion,
        status_code=201,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_create_agent_profile(body: AgentProfileInput) -> AgentProfileVersion:
        try:
            return service.create(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get(
        f"{admin_prefix}/export",
        response_model=AgentProfileExport,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_export_agent_profiles(
        profile_id: UUID | None = None,
    ) -> AgentProfileExport:
        try:
            return service.export(profile_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post(
        f"{admin_prefix}/import",
        response_model=list[AgentProfileVersion],
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_import_agent_profiles(
        body: AgentProfileExport,
    ) -> list[AgentProfileVersion]:
        try:
            return service.import_profiles(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post(
        f"{admin_prefix}/template-preview",
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_preview_agent_template(body: TemplatePreview) -> dict[str, str]:
        try:
            return {"rendered": SafeTemplateRenderer.render(body.template, body.values)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get(
        f"{admin_prefix}/{{profile_id}}",
        response_model=AgentProfileVersion,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_get_agent_profile(
        profile_id: UUID,
        version: int | None = None,
    ) -> AgentProfileVersion:
        try:
            return service.require(profile_id, version)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get(
        f"{admin_prefix}/{{profile_id}}/versions",
        response_model=list[AgentProfileVersion],
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_list_agent_profile_versions(
        profile_id: UUID,
    ) -> list[AgentProfileVersion]:
        service.require(profile_id)
        return repository.list_agent_profile_versions(profile_id)

    @router.put(
        f"{admin_prefix}/{{profile_id}}",
        response_model=AgentProfileVersion,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_update_agent_profile(
        profile_id: UUID,
        body: AgentProfileInput,
    ) -> AgentProfileVersion:
        try:
            return service.update(profile_id, body)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post(
        f"{admin_prefix}/{{profile_id}}/copy",
        response_model=AgentProfileVersion,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_copy_agent_profile(
        profile_id: UUID,
        body: ProfileCopy,
    ) -> AgentProfileVersion:
        try:
            return service.copy(profile_id, body.name)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post(
        f"{admin_prefix}/{{profile_id}}/default",
        response_model=AgentProfileVersion,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_default_agent_profile(profile_id: UUID) -> AgentProfileVersion:
        try:
            return service.set_default(profile_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post(
        f"{admin_prefix}/{{profile_id}}/rollback/{{version}}",
        response_model=AgentProfileVersion,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_rollback_agent_profile(
        profile_id: UUID,
        version: int,
    ) -> AgentProfileVersion:
        try:
            return service.rollback(profile_id, version)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.delete(
        f"{admin_prefix}/{{profile_id}}",
        status_code=204,
        dependencies=[Depends(context.require_admin)],
    )
    async def admin_delete_agent_profile(profile_id: UUID) -> None:
        try:
            service.delete(profile_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
