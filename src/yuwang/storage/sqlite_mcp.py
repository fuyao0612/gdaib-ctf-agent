"""SQLite 中 MCP 服务配置的持久化，不保存认证明文。"""

from __future__ import annotations

from uuid import UUID

from yuwang.storage.sqlite_common import SQLiteStore
from yuwang.tooling.mcp.models import McpServerConfig


class SQLiteMcpStore(SQLiteStore):
    def list_mcp_servers(self) -> list[McpServerConfig]:
        with self.connect() as db:
            rows = db.execute("SELECT data FROM mcp_servers ORDER BY created_at").fetchall()
        return [McpServerConfig.model_validate_json(row["data"]) for row in rows]

    def get_mcp_server(self, server_id: UUID) -> McpServerConfig | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM mcp_servers WHERE id=?", (str(server_id),)).fetchone()
        return McpServerConfig.model_validate_json(row["data"]) if row else None

    def save_mcp_server(self, value: McpServerConfig) -> McpServerConfig:
        with self._lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO mcp_servers(id,data,created_at) VALUES(?,?,?)",
                (str(value.id), value.model_dump_json(), value.created_at),
            )
        return value

    def delete_mcp_server(self, server_id: UUID) -> None:
        with self._lock, self.connect() as db:
            cursor = db.execute("DELETE FROM mcp_servers WHERE id=?", (str(server_id),))
            if cursor.rowcount == 0:
                raise KeyError("MCP 服务不存在")
