"""MCP 配置、认证保护、发现缓存和 ToolPlugin 注册协调。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from yuwang.settings.security import SecretCipher

from ..registry import ToolRegistry
from .client import McpClient
from .models import McpDeletionImpact, McpServerConfig, McpServerInput, McpServerView
from .plugin import McpToolPlugin
from .security import validate_http_config, validate_stdio_config


def _now() -> str:
    return datetime.now(UTC).isoformat()


class McpRepository(Protocol):
    def list_mcp_servers(self) -> list[McpServerConfig]: ...
    def get_mcp_server(self, server_id: UUID) -> McpServerConfig | None: ...
    def save_mcp_server(self, value: McpServerConfig) -> McpServerConfig: ...
    def delete_mcp_server(self, server_id: UUID) -> None: ...
    def get_mcp_deletion_impact(self, server_id: UUID) -> tuple[int, int]: ...


class McpService:
    def __init__(self, repository: McpRepository, cipher: SecretCipher, client: McpClient) -> None:
        self.repository = repository
        self.cipher = cipher
        self.client = client

    def list_servers(self) -> list[McpServerView]:
        return [value.public_view() for value in self.repository.list_mcp_servers()]

    def get(self, server_id: UUID) -> McpServerConfig:
        value = self.repository.get_mcp_server(server_id)
        if not value:
            raise KeyError("MCP 服务不存在")
        return value

    def create(self, value: McpServerInput) -> McpServerView:
        self._validate_transport(value)
        config = McpServerConfig(
            **value.model_dump(exclude={"auth_token"}),
            encrypted_auth_token=self.cipher.encrypt(value.auth_token) if value.auth_token else "",
        )
        self.repository.save_mcp_server(config)
        return config.public_view()

    def update(self, server_id: UUID, value: McpServerInput) -> McpServerView:
        current = self.get(server_id)
        self._validate_transport(value)
        data = value.model_dump(exclude={"auth_token"})
        current = current.model_copy(
            update={
                **data,
                "encrypted_auth_token": (
                    self.cipher.encrypt(value.auth_token)
                    if value.auth_token
                    else current.encrypted_auth_token
                ),
                "updated_at": _now(),
                "health_status": "untested" if not value.enabled else current.health_status,
            }
        )
        self.repository.save_mcp_server(current)
        return current.public_view()

    def delete(self, server_id: UUID, registry: ToolRegistry) -> None:
        config = self.get(server_id)
        impact = self.deletion_impact(server_id)
        if impact.blocking_reasons:
            raise ValueError("；".join(impact.blocking_reasons))
        registry.unregister_source(f"mcp:{config.id}")
        self.repository.delete_mcp_server(server_id)

    def deletion_impact(self, server_id: UUID) -> McpDeletionImpact:
        config = self.get(server_id)
        active_runs, historical_snapshots = self.repository.get_mcp_deletion_impact(server_id)
        reasons = (
            [f"有 {active_runs} 个运行中的任务仍引用此 MCP 服务，需先停止或完成"]
            if active_runs
            else []
        )
        return McpDeletionImpact(
            id=config.id,
            name=config.name,
            active_run_count=active_runs,
            historical_snapshot_count=historical_snapshots,
            blocking_reasons=reasons,
        )

    async def refresh(self, server_id: UUID, registry: ToolRegistry) -> list[dict[str, object]]:
        config = self.get(server_id)
        if not config.enabled:
            config.health_status = "disabled"
            config.updated_at = _now()
            self.repository.save_mcp_server(config)
            registry.unregister_source(f"mcp:{config.id}")
            return []
        try:
            token = self._decrypt_auth(config)
            tools = await self.client.list_tools(config, token)
            filtered = [item for item in tools if self._allowed(config, item)]
            plugins = []
            for item in filtered:
                async def call(
                    name: str,
                    arguments: dict[str, object],
                    config: McpServerConfig = config,
                ) -> dict[str, object]:
                    return await self._call(config, name, arguments)

                plugins.append(
                    McpToolPlugin(
                        server_id=str(config.id),
                        server_name=config.name,
                        tool=item,
                        call=call,
                    )
                )
            registry.unregister_source(f"mcp:{config.id}")
            for plugin in plugins:
                registry.register(plugin)
            config.health_status = "healthy"
            config.last_connected_at = _now()
            config.last_error = None
            config.updated_at = _now()
            self.repository.save_mcp_server(config)
            return [plugin.spec.model_dump(mode="json") for plugin in plugins]
        except Exception as exc:
            config.health_status = "unavailable"
            config.last_error = str(exc)[:500]
            config.updated_at = _now()
            self.repository.save_mcp_server(config)
            raise ValueError("MCP 服务连接或工具发现失败") from exc

    async def _call(
        self, config: McpServerConfig, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        return await self.client.call_tool(config, self._decrypt_auth(config), name, arguments)

    def _decrypt_auth(self, config: McpServerConfig) -> str | None:
        return self.cipher.decrypt(config.encrypted_auth_token) if config.encrypted_auth_token else None

    def _validate_transport(self, value: McpServerInput) -> None:
        config = McpServerConfig(**value.model_dump(exclude={"auth_token"}))
        if config.transport == "stdio":
            validate_stdio_config(config, self.client.allowed_commands)
        else:
            validate_http_config(config, allow_insecure_local=self.client.allow_insecure_local)

    @staticmethod
    def _allowed(config: McpServerConfig, tool: dict[str, object]) -> bool:
        name = tool.get("name")
        if not isinstance(name, str):
            return False
        if name in config.blocked_tools:
            return False
        return not config.allowed_tools or name in config.allowed_tools
