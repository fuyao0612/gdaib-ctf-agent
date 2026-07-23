"""管理员服务端会话路由。"""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from apps.api.context import ApiContext


def create_session_router(context: ApiContext) -> APIRouter:
    """创建本机管理会话的路由。"""

    router = APIRouter(prefix="/api/v1/admin/session", tags=["session"])

    @router.post("")
    async def create_admin_session(response: Response) -> dict[str, Any]:
        session_id = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        expires_at = time.time() + context.config.admin_session_ttl_seconds
        context.admin_sessions[session_id] = (expires_at, csrf)
        response.set_cookie(
            "yuwang_admin_session",
            session_id,
            max_age=context.config.admin_session_ttl_seconds,
            httponly=True,
            secure=context.config.cookie_secure,
            samesite="strict",
            path="/api/v1",
        )
        return {"status": "ok", "csrf_token": csrf, "expires_at": expires_at}

    @router.get("")
    async def get_admin_session(request: Request) -> dict[str, Any]:
        session = context.verify_session(
            request,
            request.headers.get("X-CSRF-Token"),
        )
        return {
            "authenticated": True,
            "csrf_token": session[1],
            "expires_at": session[0],
        }

    @router.delete("", status_code=204, dependencies=[Depends(context.require_admin)])
    async def delete_admin_session(request: Request, response: Response) -> None:
        session_id = request.cookies.get("yuwang_admin_session", "")
        context.admin_sessions.pop(session_id, None)
        response.delete_cookie("yuwang_admin_session", path="/api/v1")

    return router
