"""API 进程配置。

环境变量只在这里读取，避免路由模块各自解释配置。测试可以直接构造
``Settings``，生产环境则使用默认值读取 Docker 注入的变量。
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """FastAPI 适配层所需的最小运行配置。"""

    database_path: Path = Path(os.getenv("YUWANG_DATABASE_PATH", "data/yuwang.db"))
    artifact_root: Path = Path(os.getenv("YUWANG_ARTIFACT_ROOT", "data/artifacts"))
    cors_origins: list[str] = Field(
        default_factory=lambda: os.getenv(
            "YUWANG_CORS_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173,"
            "http://127.0.0.1:8080,http://localhost:8080",
        ).split(",")
    )
    max_request_bytes: int = 6 * 1024 * 1024
    master_key: str = os.getenv("YUWANG_MASTER_KEY", "")
    allow_insecure_local_provider: bool = (
        os.getenv("YUWANG_ALLOW_INSECURE_LOCAL_PROVIDER", "false").lower() == "true"
    )
    # 仅允许管理员通过部署环境声明的程序启动 stdio MCP，绝不接受 Shell。
    mcp_stdio_allowed_commands: list[str] = Field(
        default_factory=lambda: [
            value
            for value in os.getenv("YUWANG_MCP_STDIO_ALLOWED_COMMANDS", "").split(os.pathsep)
            if value
        ]
    )
    allow_insecure_local_mcp: bool = (
        os.getenv("YUWANG_ALLOW_INSECURE_LOCAL_MCP", "false").lower() == "true"
    )
    sandbox_url: str = os.getenv("YUWANG_SANDBOX_URL", "http://tool-sandbox:8081")
    admin_session_ttl_seconds: int = 8 * 60 * 60
    cookie_secure: bool = os.getenv("YUWANG_COOKIE_SECURE", "false").lower() == "true"
