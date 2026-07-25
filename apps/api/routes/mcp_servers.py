"""MCP 服务管理路由：只处理参数、会话与错误转换。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from apps.api.context import ApiContext
from yuwang.tooling.mcp import McpDeletionImpact, McpServerInput, McpServerView


def create_mcp_server_router(context: ApiContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin/settings/mcp-servers", tags=["mcp"])

    @router.get("", response_model=list[McpServerView], dependencies=[Depends(context.require_admin)])
    async def list_servers() -> list[McpServerView]:
        return context.get_mcp_service().list_servers()

    @router.post("", response_model=McpServerView, status_code=201, dependencies=[Depends(context.require_admin)])
    async def create_server(body: McpServerInput) -> McpServerView:
        try:
            return context.get_mcp_service().create(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.put("/{server_id}", response_model=McpServerView, dependencies=[Depends(context.require_admin)])
    async def update_server(server_id: UUID, body: McpServerInput) -> McpServerView:
        try:
            return context.get_mcp_service().update(server_id, body)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get(
        "/{server_id}/deletion-impact",
        response_model=McpDeletionImpact,
        dependencies=[Depends(context.require_admin)],
    )
    async def deletion_impact(server_id: UUID) -> McpDeletionImpact:
        try:
            return context.get_mcp_service().deletion_impact(server_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.delete("/{server_id}", status_code=204, dependencies=[Depends(context.require_admin)])
    async def delete_server(server_id: UUID) -> None:
        try:
            context.get_mcp_service().delete(server_id, context.registry)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/stdio-commands", dependencies=[Depends(context.require_admin)])
    async def stdio_commands() -> dict[str, list[str]]:
        # 只公开已经由部署管理员允许的可执行文件，页面不接受任意 Shell 命令。
        return {"commands": sorted(context.config.mcp_stdio_allowed_commands)}

    @router.post("/{server_id}/refresh", dependencies=[Depends(context.require_admin)])
    async def refresh_server(server_id: UUID) -> dict[str, object]:
        try:
            tools = await context.get_mcp_service().refresh(server_id, context.registry)
            return {"tools": tools}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(502, str(exc)) from exc

    return router
