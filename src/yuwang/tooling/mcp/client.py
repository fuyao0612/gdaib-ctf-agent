"""基于官方 MCP Python SDK 的短连接客户端。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from .models import McpServerConfig
from .security import assert_resolved_endpoint_is_safe, validate_http_config, validate_stdio_config


class McpClient:
    """每次连接独立失败；下一次操作会重新建连，不影响本地工具或其他服务。"""

    def __init__(
        self,
        *,
        allowed_commands: set[str],
        allow_insecure_local: bool = False,
    ) -> None:
        self.allowed_commands = allowed_commands
        self.allow_insecure_local = allow_insecure_local

    @asynccontextmanager
    async def session(
        self, config: McpServerConfig, auth_token: str | None
    ) -> AsyncIterator[ClientSession]:
        if config.transport == "stdio":
            validate_stdio_config(config, self.allowed_commands)
            parameters = StdioServerParameters(command=config.command or "", args=config.args)
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=config.connect_timeout_seconds),
                ) as session:
                    await asyncio.wait_for(session.initialize(), config.connect_timeout_seconds)
                    yield session
            return
        validate_http_config(config, allow_insecure_local=self.allow_insecure_local)
        assert config.url is not None
        assert_resolved_endpoint_is_safe(
            config.url, allow_insecure_local=self.allow_insecure_local
        )
        headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None
        async with httpx.AsyncClient(
            headers=headers,
            timeout=config.connect_timeout_seconds,
            verify=True,
            follow_redirects=False,
        ) as http_client:
            async with streamable_http_client(
                config.url,
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=config.call_timeout_seconds),
                ) as session:
                    await asyncio.wait_for(session.initialize(), config.connect_timeout_seconds)
                    yield session

    async def list_tools(
        self, config: McpServerConfig, auth_token: str | None
    ) -> list[dict[str, Any]]:
        async with self.session(config, auth_token) as session:
            result = await asyncio.wait_for(session.list_tools(), config.call_timeout_seconds)
        return [item.model_dump(mode="json") for item in result.tools]

    async def call_tool(
        self,
        config: McpServerConfig,
        auth_token: str | None,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        async with self.session(config, auth_token) as session:
            result = await asyncio.wait_for(
                session.call_tool(name, arguments), config.call_timeout_seconds
            )
        return {
            "content": [item.model_dump(mode="json") for item in result.content],
            "structured_content": getattr(result, "structuredContent", None),
            "is_error": bool(getattr(result, "isError", False)),
        }
