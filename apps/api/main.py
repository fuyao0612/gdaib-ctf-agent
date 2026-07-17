"""御网智元 FastAPI 应用装配入口。

本模块只创建依赖、安装中间件并挂载业务路由。具体 HTTP 行为位于
``apps.api.routes``，Agent 与数据库规则仍由各自核心模块负责。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api.config import Settings
from apps.api.context import ApiContext
from apps.api.routes import (
    create_agent_profile_router,
    create_chat_router,
    create_health_router,
    create_provider_router,
    create_report_router,
    create_run_router,
    create_session_router,
    create_thread_router,
)
from yuwang import __version__

PUBLIC_API_PATHS = {
    "/api/v1/health",
    "/api/v1/readiness",
    "/api/v1/setup/status",
    "/api/v1/provider-presets",
    "/api/v1/admin/session",
    "/api/v1/openapi.json",
    "/api/docs",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建一个依赖隔离的应用实例，供生产启动和集成测试共同使用。"""

    context = ApiContext(settings or Settings())
    application = FastAPI(
        title="御网智元 API",
        version=__version__,
        lifespan=context.lifespan,
        docs_url="/api/docs",
        openapi_url="/api/v1/openapi.json",
    )
    application.state.repository = context.repository
    application.state.settings = context.config
    application.state.registry = context.registry
    application.state.tasks = context.tasks
    application.state.context = context
    application.add_middleware(
        CORSMiddleware,
        allow_origins=context.config.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "X-CSRF-Token"],
        allow_credentials=True,
    )

    @application.middleware("http")
    async def request_size_limit(request: Request, call_next: Any) -> Any:
        """在解析 JSON/上传文件前拒绝明显超限的请求。"""

        length = request.headers.get("content-length")
        if length and int(length) > context.config.max_request_bytes:
            return JSONResponse(
                status_code=413,
                content={"error": {"code": "request_too_large", "message": "请求体超过限制"}},
            )
        return await call_next(request)

    @application.middleware("http")
    async def protect_workbench(request: Request, call_next: Any) -> Any:
        """单用户工作台统一复用服务端会话，公开范围仅限启动所需端点。"""

        if request.url.path.startswith("/api/v1/") and request.url.path not in PUBLIC_API_PATHS:
            try:
                context.verify_session(
                    request,
                    request.headers.get("Authorization"),
                    request.headers.get("X-CSRF-Token"),
                )
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={
                        "error": {
                            "code": f"http_{exc.status_code}",
                            "message": str(exc.detail),
                        }
                    },
                )
        return await call_next(request)

    @application.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": f"http_{exc.status_code}", "message": detail}},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": "请求参数校验失败"}},
        )

    # 路由装配顺序不改变路径契约；每个工厂都绑定当前应用自己的上下文。
    application.include_router(create_health_router(context))
    application.include_router(create_session_router(context))
    application.include_router(create_thread_router(context))
    application.include_router(create_chat_router(context))
    application.include_router(create_run_router(context))
    application.include_router(create_report_router(context))
    application.include_router(create_agent_profile_router(context))
    application.include_router(create_provider_router(context))
    return application


app = create_app()


__all__ = ["Settings", "app", "create_app"]
