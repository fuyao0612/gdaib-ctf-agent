"""声明式 Skills 的设置与只读选择接口。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from apps.api.context import ApiContext
from yuwang.settings import SkillDefinition, SkillInput


def create_skill_router(context: ApiContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["skills"])
    admin_prefix = "/admin/settings/skills"

    @router.get("/skills", response_model=list[SkillDefinition])
    async def list_enabled_skills() -> list[SkillDefinition]:
        return context.skill_service.list_skills(enabled_only=True)

    @router.get(
        admin_prefix,
        response_model=list[SkillDefinition],
        dependencies=[Depends(context.require_admin)],
    )
    async def list_skills() -> list[SkillDefinition]:
        return context.skill_service.list_skills()

    @router.post(
        admin_prefix,
        response_model=SkillDefinition,
        status_code=201,
        dependencies=[Depends(context.require_admin)],
    )
    async def create_skill(body: SkillInput) -> SkillDefinition:
        return context.skill_service.create(body)

    @router.put(
        f"{admin_prefix}/{{skill_id}}",
        response_model=SkillDefinition,
        dependencies=[Depends(context.require_admin)],
    )
    async def update_skill(skill_id: UUID, body: SkillInput) -> SkillDefinition:
        try:
            return context.skill_service.update(skill_id, body)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.delete(
        f"{admin_prefix}/{{skill_id}}",
        status_code=204,
        dependencies=[Depends(context.require_admin)],
    )
    async def delete_skill(skill_id: UUID) -> None:
        try:
            context.skill_service.delete(skill_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    return router
