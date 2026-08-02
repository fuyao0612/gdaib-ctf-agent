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
    create_evaluation_router,
    create_health_router,
    create_mcp_server_router,
    create_message_router,
    create_provider_router,
    create_report_router,
    create_run_router,
    create_session_router,
    create_skill_router,
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
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        field_labels = {
            "name": "名称",
            "budget": "预算",
            "max_steps": "最大步骤",
            "max_model_calls": "最大模型调用次数",
            "max_tool_calls": "最大工具调用次数",
            "max_tokens": "最大 Token",
            "max_model_cost": "最大模型费用",
            "max_duration_seconds": "总时长（秒）",
            "step_timeout_seconds": "单步超时（秒）",
            "context_policy": "上下文策略",
            "recent_message_limit": "最近消息数量",
            "text_attachment_char_limit": "附件字符数",
            "memory_policy": "记忆策略",
            "max_facts": "最大事实数量",
        }

        def validation_message(error: dict[str, object]) -> str:
            context = error.get("ctx")
            context = context if isinstance(context, dict) else {}
            error_type = str(error.get("type", ""))
            if error_type == "missing":
                return "此项为必填"
            if error_type == "string_too_short":
                return f"长度不能少于 {context.get('min_length')} 个字符"
            if error_type == "string_too_long":
                return f"长度不能超过 {context.get('max_length')} 个字符"
            if error_type == "greater_than_equal":
                return f"必须大于或等于 {context.get('ge')}"
            if error_type == "greater_than":
                return f"必须大于 {context.get('gt')}"
            if error_type == "less_than_equal":
                return f"不能大于 {context.get('le')}"
            if error_type == "less_than":
                return f"必须小于 {context.get('lt')}"
            if error_type in {"int_parsing", "float_parsing", "decimal_parsing"}:
                return "必须是数字"
            if error_type == "bool_parsing":
                return "必须是布尔值"
            if error_type == "string_type":
                return "必须是文本"
            return str(error.get("msg", "参数无效")).removeprefix("Value error, ")

        details: list[str] = []
        for error in exc.errors():
            location = ".".join(
                str(item) for item in error.get("loc", ()) if item not in {"body", "query"}
            )
            display_location = ".".join(
                field_labels.get(item, item) for item in location.split(".") if item
            )
            message = validation_message(error)
            details.append(f"{display_location}：{message}" if display_location else message)
        message = "请求参数校验失败"
        if details:
            message += "：" + "；".join(dict.fromkeys(details))
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": message}},
        )

    # 路由装配顺序不改变路径契约；每个工厂都绑定当前应用自己的上下文。
    application.include_router(create_health_router(context))
    application.include_router(create_session_router(context))
    application.include_router(create_skill_router(context))
    application.include_router(create_thread_router(context))
    application.include_router(create_message_router(context))
    application.include_router(create_run_router(context))
    application.include_router(create_report_router(context))
    application.include_router(create_evaluation_router(context))
    application.include_router(create_agent_profile_router(context))
    application.include_router(create_provider_router(context))
    application.include_router(create_mcp_server_router(context))
    return application


app = create_app()


__all__ = ["Settings", "app", "create_app"]
