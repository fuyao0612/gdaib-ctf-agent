"""健康、就绪状态与公开能力路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from apps.api.context import ApiContext
from yuwang import __version__


def create_health_router(context: ApiContext) -> APIRouter:
    """创建无需登录即可访问的部署检查路由。"""

    router = APIRouter(prefix="/api/v1")

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @router.get("/setup/status")
    async def setup_status() -> dict[str, Any]:
        checks = context.deployment_checks()
        return {"configured": all(checks.values()), "checks": checks, "version": __version__}

    @router.get("/readiness")
    async def readiness() -> JSONResponse:
        checks = context.deployment_checks()
        ready = all(checks.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "checks": checks},
        )

    @router.get("/tools")
    async def tools() -> list[dict[str, Any]]:
        return [spec.model_dump(mode="json") for spec in context.registry.specs()]

    return router
