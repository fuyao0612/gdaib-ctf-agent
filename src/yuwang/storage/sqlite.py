"""SQLite 数据访问层：显式保存状态、检查点、快照和完整审计链。"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from yuwang.domain.models import (
    ACTIVE_RUN_STATUSES,
    Event,
    EventType,
    EvidenceRecord,
    ModelCall,
    Run,
    RunCheckpoint,
    TaskSpec,
    ToolCall,
)
from yuwang.settings.models import ProviderConfig
from yuwang.settings.profiles import AgentProfileVersion
from yuwang.storage.sqlite_control import SQLiteControlStore
from yuwang.storage.sqlite_settings import SQLiteSettingsStore
from yuwang.storage.sqlite_workspace import SQLiteWorkspaceStore


class SQLiteRepository(SQLiteWorkspaceStore, SQLiteSettingsStore, SQLiteControlStore):
    """显式 SQLite 数据访问层。

    输入和输出均为领域模型，调用方看不到 SQL 行。未来替换数据库时实现
    `AgentRepository` 与设置仓储协议即可，不应修改 Agent 节点。
    """

    def migrate(self) -> None:
        """幂等创建当前 Schema；复杂版本迁移应继续记录到 `schema_migrations`。"""

        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (1);
                CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, created_at);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_runs_thread ON runs(thread_id, created_at);
                CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence INTEGER NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(run_id, sequence));
                CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, run_id TEXT, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reports(run_id TEXT PRIMARY KEY, markdown TEXT NOT NULL, json_data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS provider_configs(id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS run_tasks(run_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS run_checkpoints(run_id TEXT NOT NULL, checkpoint_sequence INTEGER NOT NULL, node TEXT NOT NULL, state_schema_version TEXT NOT NULL, elapsed_seconds REAL NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(run_id,checkpoint_sequence));
                CREATE TABLE IF NOT EXISTS model_calls(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tool_calls(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS provider_snapshots(run_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS agent_profile_versions(profile_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(profile_id,version));
                CREATE TABLE IF NOT EXISTS run_agent_profiles(run_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, kind TEXT NOT NULL, enabled INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS task_briefs(run_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id,version));
                CREATE TABLE IF NOT EXISTS run_plan_revisions(run_id TEXT NOT NULL, version INTEGER NOT NULL, source TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id,version));
                CREATE TABLE IF NOT EXISTS run_control_requests(run_id TEXT NOT NULL, request_id TEXT NOT NULL, action TEXT NOT NULL, payload_hash TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id,request_id));
                CREATE TABLE IF NOT EXISTS run_guidance(run_id TEXT NOT NULL, sequence INTEGER NOT NULL, request_id TEXT NOT NULL, data TEXT NOT NULL, consumed_at TEXT, created_at TEXT NOT NULL, PRIMARY KEY(run_id,sequence), UNIQUE(run_id,request_id));
                CREATE TABLE IF NOT EXISTS run_pause_requests(run_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, requested_at TEXT NOT NULL, consumed_at TEXT);
                CREATE TABLE IF NOT EXISTS chat_requests(request_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, status TEXT NOT NULL, user_message_id TEXT NOT NULL, assistant_message_id TEXT, error TEXT, created_at TEXT NOT NULL);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (7);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (6);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (4);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (5);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (2);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (3);
                """
            )
            rows = db.execute("SELECT id,data FROM threads").fetchall()
            for row in rows:
                data = json.loads(row["data"])
                if "interaction_mode" not in data:
                    data["interaction_mode"] = "agent"
                    db.execute(
                        "UPDATE threads SET data=? WHERE id=?",
                        (json.dumps(data, ensure_ascii=False), row["id"]),
                    )
            db.execute(
                "UPDATE chat_requests SET status='failed',error=? WHERE status='running'",
                ("服务重启中断生成，请重试",),
            )

    def save_run(self, value: Run) -> Run:
        with self._lock, self.connect() as db:
            if value.status in ACTIVE_RUN_STATUSES:
                active = db.execute(
                    "SELECT id FROM runs WHERE thread_id=? AND status IN "
                    "('queued','running','waiting_input','waiting_clarification',"
                    "'waiting_approval','paused') AND id<>?",
                    (str(value.thread_id), str(value.id)),
                ).fetchone()
                if active:
                    raise ValueError("thread already has an active run")
            db.execute(
                "INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?)",
                (
                    str(value.id),
                    str(value.thread_id),
                    str(value.status),
                    self._dump(value),
                    value.created_at.isoformat(),
                ),
            )
        return value

    def get_run(self, run_id: UUID | str) -> Run | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM runs WHERE id=?", (str(run_id),)).fetchone()
        return self._load(Run, row["data"]) if row else None

    def list_runs(self, thread_id: UUID | str) -> list[Run]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM runs WHERE thread_id=? ORDER BY created_at", (str(thread_id),)
            ).fetchall()
        return [self._load(Run, row["data"]) for row in rows]

    def request_stop(self, run_id: UUID | str) -> Run:
        run = self.get_run(run_id)
        if not run:
            raise KeyError("run not found")
        run.stop_requested = True
        return self.save_run(run)

    def append_event(self, event: Event) -> Event:
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            expected = db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM events WHERE run_id=?",
                (str(event.run_id),),
            ).fetchone()["n"]
            if event.sequence != expected:
                raise ValueError(f"event sequence must be {expected}")
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?)",
                (
                    str(event.event_id),
                    str(event.run_id),
                    event.sequence,
                    str(event.type),
                    self._dump(event),
                ),
            )
        return event

    def create_event(
        self,
        run_id: UUID,
        event_type: EventType,
        summary: str,
        payload: dict[str, Any],
    ) -> Event:
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sequence = int(
                db.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM events WHERE run_id=?",
                    (str(run_id),),
                ).fetchone()["n"]
            )
            event = Event(
                run_id=run_id,
                sequence=sequence,
                type=event_type,
                summary=summary,
                payload=payload,
            )
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?)",
                (
                    str(event.event_id),
                    str(run_id),
                    sequence,
                    str(event.type),
                    event.model_dump_json(),
                ),
            )
        return event

    def next_sequence(self, run_id: UUID | str) -> int:
        with self.connect() as db:
            return int(
                db.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM events WHERE run_id=?",
                    (str(run_id),),
                ).fetchone()["n"]
            )

    def list_events(self, run_id: UUID | str, after: int = 0) -> list[Event]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM events WHERE run_id=? AND sequence>? ORDER BY sequence",
                (str(run_id), after),
            ).fetchall()
        return [self._load(Event, row["data"]) for row in rows]

    def save_checkpoint(self, run_id: UUID | str, node: str, data: dict[str, Any]) -> None:
        elapsed = float(data.get("elapsed_seconds", 0.0))
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sequence = int(
                db.execute(
                    "SELECT COALESCE(MAX(checkpoint_sequence),0)+1 AS n FROM run_checkpoints WHERE run_id=?",
                    (str(run_id),),
                ).fetchone()["n"]
            )
            checkpoint = RunCheckpoint(
                run_id=UUID(str(run_id)),
                checkpoint_sequence=sequence,
                node=node,
                state=data,
                elapsed_seconds=elapsed,
            )
            db.execute(
                "INSERT INTO run_checkpoints(run_id,checkpoint_sequence,node,state_schema_version,elapsed_seconds,data) VALUES(?,?,?,?,?,?)",
                (
                    str(run_id),
                    sequence,
                    node,
                    checkpoint.state_schema_version,
                    elapsed,
                    checkpoint.model_dump_json(),
                ),
            )

    def latest_checkpoint(self, run_id: UUID | str) -> RunCheckpoint | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM run_checkpoints WHERE run_id=? ORDER BY checkpoint_sequence DESC LIMIT 1",
                (str(run_id),),
            ).fetchone()
        return RunCheckpoint.model_validate_json(row["data"]) if row else None

    def list_checkpoints(self, run_id: UUID | str) -> list[RunCheckpoint]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM run_checkpoints WHERE run_id=? ORDER BY checkpoint_sequence",
                (str(run_id),),
            ).fetchall()
        return [RunCheckpoint.model_validate_json(row["data"]) for row in rows]

    def save_report(self, run_id: UUID | str, markdown: str, data: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO reports VALUES(?,?,?)",
                (str(run_id), markdown, json.dumps(data, ensure_ascii=False, default=str)),
            )

    def get_report(self, run_id: UUID | str) -> tuple[str, dict[str, Any]] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT markdown,json_data FROM reports WHERE run_id=?", (str(run_id),)
            ).fetchone()
        return (row["markdown"], json.loads(row["json_data"])) if row else None

    def save_run_task(self, run_id: UUID, value: TaskSpec) -> None:
        with self.connect() as db:
            existing = db.execute(
                "SELECT data FROM run_tasks WHERE run_id=?", (str(run_id),)
            ).fetchone()
            serialized = value.model_dump_json()
            if existing and existing["data"] != serialized:
                raise ValueError("Run 的 TaskSpec 快照不可变")
            db.execute(
                "INSERT OR IGNORE INTO run_tasks(run_id,data) VALUES(?,?)",
                (str(run_id), serialized),
            )

    def get_run_task(self, run_id: UUID | str) -> TaskSpec | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM run_tasks WHERE run_id=?", (str(run_id),)).fetchone()
        return TaskSpec.model_validate_json(row["data"]) if row else None

    def save_model_call(self, value: ModelCall) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO model_calls(id,run_id,data) VALUES(?,?,?)",
                (str(value.id), str(value.run_id), value.model_dump_json()),
            )

    def list_model_calls(self, run_id: UUID | str) -> list[ModelCall]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM model_calls WHERE run_id=? ORDER BY rowid", (str(run_id),)
            ).fetchall()
        return [ModelCall.model_validate_json(row["data"]) for row in rows]

    def save_tool_call(self, value: ToolCall) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO tool_calls(id,run_id,status,data) VALUES(?,?,?,?)",
                (str(value.id), str(value.run_id), str(value.status), value.model_dump_json()),
            )

    def list_tool_calls(self, run_id: UUID | str) -> list[ToolCall]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM tool_calls WHERE run_id=? ORDER BY rowid", (str(run_id),)
            ).fetchall()
        return [ToolCall.model_validate_json(row["data"]) for row in rows]

    def save_evidence(self, value: EvidenceRecord) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO evidence(id,run_id,data) VALUES(?,?,?)",
                (str(value.id), str(value.run_id), value.model_dump_json()),
            )

    def list_evidence(self, run_id: UUID | str) -> list[EvidenceRecord]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM evidence WHERE run_id=? ORDER BY rowid", (str(run_id),)
            ).fetchall()
        return [EvidenceRecord.model_validate_json(row["data"]) for row in rows]

    def save_provider_snapshot(self, run_id: UUID, values: list[ProviderConfig]) -> None:
        serialized = json.dumps(
            [value.model_dump(mode="json") for value in values], ensure_ascii=False
        )
        with self.connect() as db:
            existing = db.execute(
                "SELECT data FROM provider_snapshots WHERE run_id=?", (str(run_id),)
            ).fetchone()
            if existing and existing["data"] != serialized:
                raise ValueError("Run 的 Provider 快照不可变")
            db.execute(
                "INSERT OR IGNORE INTO provider_snapshots(run_id,data) VALUES(?,?)",
                (str(run_id), serialized),
            )

    def get_provider_snapshot(self, run_id: UUID | str) -> list[ProviderConfig]:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM provider_snapshots WHERE run_id=?", (str(run_id),)
            ).fetchone()
        if not row:
            return []
        return [ProviderConfig.model_validate(value) for value in json.loads(row["data"])]

    def save_run_agent_profile(self, run_id: UUID, value: AgentProfileVersion) -> None:
        serialized = value.model_dump_json()
        with self.connect() as db:
            existing = db.execute(
                "SELECT data FROM run_agent_profiles WHERE run_id=?", (str(run_id),)
            ).fetchone()
            if existing and existing["data"] != serialized:
                raise ValueError("Run 的 AgentProfile 快照不可变")
            db.execute(
                "INSERT OR IGNORE INTO run_agent_profiles(run_id,data) VALUES(?,?)",
                (str(run_id), serialized),
            )

    def get_run_agent_profile(self, run_id: UUID) -> AgentProfileVersion | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM run_agent_profiles WHERE run_id=?", (str(run_id),)
            ).fetchone()
        return AgentProfileVersion.model_validate_json(row["data"]) if row else None
