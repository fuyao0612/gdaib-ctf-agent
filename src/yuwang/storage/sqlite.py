from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel

from yuwang.domain.models import (
    Artifact,
    Event,
    EventType,
    EvidenceRecord,
    Message,
    ModelCall,
    Run,
    RunCheckpoint,
    RunStatus,
    TaskSpec,
    Thread,
    ToolCall,
)
from yuwang.settings.models import AgentDefaults, ProviderConfig

T = TypeVar("T", bound=BaseModel)


class SQLiteRepository:
    """Small explicit data access layer; callers never depend on SQLite details."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def migrate(self) -> None:
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
                """
            )

    @staticmethod
    def _dump(model: BaseModel) -> str:
        return model.model_dump_json()

    @staticmethod
    def _load(model: type[T], raw: str) -> T:
        return model.model_validate_json(raw)

    def save_thread(self, value: Thread) -> Thread:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO threads VALUES(?,?,?)",
                (str(value.id), self._dump(value), value.created_at.isoformat()),
            )
        return value

    def get_thread(self, thread_id: UUID | str) -> Thread | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM threads WHERE id=?", (str(thread_id),)).fetchone()
        return self._load(Thread, row["data"]) if row else None

    def list_threads(self) -> list[Thread]:
        with self.connect() as db:
            rows = db.execute("SELECT data FROM threads ORDER BY created_at DESC").fetchall()
        return [self._load(Thread, row["data"]) for row in rows]

    def save_message(self, value: Message) -> Message:
        with self.connect() as db:
            db.execute(
                "INSERT INTO messages VALUES(?,?,?,?)",
                (
                    str(value.id),
                    str(value.thread_id),
                    self._dump(value),
                    value.created_at.isoformat(),
                ),
            )
        return value

    def list_messages(self, thread_id: UUID | str) -> list[Message]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM messages WHERE thread_id=? ORDER BY created_at", (str(thread_id),)
            ).fetchall()
        return [self._load(Message, row["data"]) for row in rows]

    def save_run(self, value: Run) -> Run:
        with self._lock, self.connect() as db:
            if value.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
                active = db.execute(
                    "SELECT id FROM runs WHERE thread_id=? AND status IN ('queued','running') AND id<>?",
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

    def save_artifact(self, value: Artifact) -> Artifact:
        with self.connect() as db:
            db.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?,?)",
                (
                    str(value.id),
                    str(value.thread_id),
                    str(value.run_id) if value.run_id else None,
                    self._dump(value),
                    value.created_at.isoformat(),
                ),
            )
        return value

    def get_artifact(self, artifact_id: UUID | str) -> Artifact | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM artifacts WHERE id=?", (str(artifact_id),)
            ).fetchone()
        return self._load(Artifact, row["data"]) if row else None

    def list_artifacts(self, thread_id: UUID | str) -> list[Artifact]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM artifacts WHERE thread_id=? ORDER BY created_at",
                (str(thread_id),),
            ).fetchall()
        return [self._load(Artifact, row["data"]) for row in rows]

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

    def save_provider_config(self, value: ProviderConfig) -> ProviderConfig:
        with self._lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO provider_configs VALUES(?,?,?)",
                (str(value.id), value.model_dump_json(), value.created_at),
            )
        return value

    def get_provider_config(self, provider_id: UUID | str) -> ProviderConfig | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM provider_configs WHERE id=?", (str(provider_id),)
            ).fetchone()
        return ProviderConfig.model_validate_json(row["data"]) if row else None

    def list_provider_configs(self) -> list[ProviderConfig]:
        with self.connect() as db:
            rows = db.execute("SELECT data FROM provider_configs ORDER BY created_at").fetchall()
        return [ProviderConfig.model_validate_json(row["data"]) for row in rows]

    def set_default_provider(self, provider_id: UUID) -> None:
        with self._lock:
            values = self.list_provider_configs()
            if not any(value.id == provider_id for value in values):
                raise KeyError("Provider 配置不存在")
            for value in values:
                desired = value.id == provider_id
                if value.is_default != desired:
                    value.is_default = desired
                    self.save_provider_config(value)

    def delete_provider_config(self, provider_id: UUID) -> None:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM provider_configs WHERE id=?", (str(provider_id),))
            if cursor.rowcount == 0:
                raise KeyError("Provider 配置不存在")

    def get_agent_defaults(self) -> AgentDefaults:
        with self.connect() as db:
            row = db.execute("SELECT data FROM app_settings WHERE key='agent_defaults'").fetchone()
        return AgentDefaults.model_validate_json(row["data"]) if row else AgentDefaults()

    def save_agent_defaults(self, value: AgentDefaults) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO app_settings(key,data) VALUES('agent_defaults',?)",
                (value.model_dump_json(),),
            )

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
