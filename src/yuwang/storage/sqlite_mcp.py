"""SQLite 中 MCP 服务配置的持久化，不保存认证明文。"""

from __future__ import annotations

import json
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

    def get_mcp_deletion_impact(self, server_id: UUID) -> tuple[int, int]:
        """按保存的 TaskSpec 快照计算引用，避免依赖当前注册表的易变状态。"""

        source = f"mcp:{server_id}"
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT tasks.data AS task_data, runs.status AS run_status
                FROM run_tasks AS tasks
                JOIN runs ON runs.id=tasks.run_id
                """
            ).fetchall()
        referenced = [
            row
            for row in rows
            if any(
                item.get("source") == source
                for item in json.loads(row["task_data"]).get("tool_snapshots", [])
            )
        ]
        active_statuses = {
            "queued",
            "running",
            "waiting_input",
            "waiting_clarification",
            "waiting_approval",
            "paused",
        }
        return (
            sum(row["run_status"] in active_statuses for row in referenced),
            len(referenced),
        )
