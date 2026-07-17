"""对话工作区存储：线程、消息、附件与可控记忆。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from yuwang.domain.models import Artifact, MemoryRecord, Message, Thread
from yuwang.storage.sqlite_common import SQLiteStore


class SQLiteWorkspaceStore(SQLiteStore):
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

    def delete_thread(self, thread_id: UUID | str) -> None:
        """跨分区清理必须处于一个事务，避免删除会话后留下孤立审计。"""

        key = str(thread_id)
        with self.connect() as db:
            run_rows = db.execute("SELECT id FROM runs WHERE thread_id=?", (key,)).fetchall()
            for run_id in [row["id"] for row in run_rows]:
                for table in (
                    "events",
                    "reports",
                    "run_tasks",
                    "run_checkpoints",
                    "model_calls",
                    "tool_calls",
                    "evidence",
                    "provider_snapshots",
                    "run_agent_profiles",
                    "task_briefs",
                    "run_plan_revisions",
                    "run_control_requests",
                    "run_guidance",
                    "run_pause_requests",
                ):
                    db.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
            db.execute("DELETE FROM messages WHERE thread_id=?", (key,))
            db.execute("DELETE FROM artifacts WHERE thread_id=?", (key,))
            db.execute("DELETE FROM memories WHERE thread_id=?", (key,))
            db.execute("DELETE FROM runs WHERE thread_id=?", (key,))
            db.execute("DELETE FROM threads WHERE id=?", (key,))

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
                "SELECT data FROM messages WHERE thread_id=? ORDER BY created_at",
                (str(thread_id),),
            ).fetchall()
        return [self._load(Message, row["data"]) for row in rows]

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

    def save_memory(self, value: MemoryRecord) -> MemoryRecord:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO memories VALUES(?,?,?,?,?,?)",
                (
                    str(value.id),
                    str(value.thread_id),
                    value.kind,
                    int(value.enabled),
                    value.model_dump_json(),
                    value.created_at.isoformat(),
                ),
            )
        return value

    def list_memories(
        self, thread_id: UUID | str, enabled_only: bool = True
    ) -> list[MemoryRecord]:
        query = "SELECT data FROM memories WHERE thread_id=?"
        parameters: list[Any] = [str(thread_id)]
        if enabled_only:
            query += " AND enabled=1"
        query += " ORDER BY created_at"
        with self.connect() as db:
            rows = db.execute(query, parameters).fetchall()
        return [MemoryRecord.model_validate_json(row["data"]) for row in rows]

    def clear_memories(self, thread_id: UUID | str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM memories WHERE thread_id=?", (str(thread_id),))

    def delete_memory(self, memory_id: UUID | str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM memories WHERE id=?", (str(memory_id),))

    def set_memories_enabled(self, thread_id: UUID | str, enabled: bool) -> None:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM memories WHERE thread_id=?", (str(thread_id),)
            ).fetchall()
            for row in rows:
                value = MemoryRecord.model_validate_json(row["data"])
                value.enabled = enabled
                db.execute(
                    "UPDATE memories SET enabled=?, data=? WHERE id=?",
                    (int(enabled), value.model_dump_json(), str(value.id)),
                )
