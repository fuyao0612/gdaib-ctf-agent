"""对话工作区存储：线程、消息、附件与可控记忆。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from yuwang.domain.models import Artifact, MemoryRecord, Message, MessageRole, Thread
from yuwang.storage.sqlite_common import SQLiteStore


class SQLiteWorkspaceStore(SQLiteStore):
    def has_chat_request(self, thread_id: UUID | str, request_id: UUID | str) -> bool:
        """判断 request_id 是否已属于该会话的聊天请求。

        统一消息入口会先用它重放已完成的聊天，避免后来启动的 Run 把同一次
        网络重发误分流为追加指引。
        """

        with self.connect() as db:
            row = db.execute(
                "SELECT thread_id FROM chat_requests WHERE request_id=?", (str(request_id),)
            ).fetchone()
        if not row:
            return False
        if row["thread_id"] != str(thread_id):
            raise ValueError("请求 ID 已用于其他会话")
        return True

    def begin_chat_request(
        self,
        thread_id: UUID,
        request_id: UUID,
        content: str,
        artifact_ids: list[UUID],
        retry: bool,
    ) -> tuple[Message, Message | None]:
        """幂等创建聊天用户消息；失败重试复用原消息，绝不重复插入。"""

        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT request_id FROM chat_requests WHERE thread_id=? AND status='running' AND request_id<>?",
                (str(thread_id), str(request_id)),
            ).fetchone()
            if active:
                raise ValueError("当前对话仍有回复正在生成")
            row = db.execute(
                "SELECT * FROM chat_requests WHERE request_id=?", (str(request_id),)
            ).fetchone()
            if row:
                user_row = db.execute(
                    "SELECT data FROM messages WHERE id=?", (row["user_message_id"],)
                ).fetchone()
                if not user_row:
                    raise ValueError("聊天请求对应的用户消息不存在")
                user_message = self._load(Message, user_row["data"])
                if user_message.content != content or user_message.artifact_ids != artifact_ids:
                    raise ValueError("请求 ID 已用于不同的聊天内容")
                if row["status"] == "completed" and row["assistant_message_id"]:
                    assistant_row = db.execute(
                        "SELECT data FROM messages WHERE id=?",
                        (row["assistant_message_id"],),
                    ).fetchone()
                    return user_message, self._load(Message, assistant_row["data"])
                if row["status"] == "running":
                    raise ValueError("该聊天请求仍在生成中")
                if not retry:
                    raise ValueError("上次生成失败，请使用重试操作")
                db.execute(
                    "UPDATE chat_requests SET status='running',error=NULL WHERE request_id=?",
                    (str(request_id),),
                )
                return user_message, None
            user_message = Message(
                thread_id=thread_id,
                role=MessageRole.USER,
                content=content,
                artifact_ids=artifact_ids,
            )
            db.execute(
                "INSERT INTO messages VALUES(?,?,?,?)",
                (
                    str(user_message.id),
                    str(thread_id),
                    self._dump(user_message),
                    user_message.created_at.isoformat(),
                ),
            )
            db.execute(
                "INSERT INTO chat_requests VALUES(?,?,?,?,?,?,?)",
                (
                    str(request_id),
                    str(thread_id),
                    "running",
                    str(user_message.id),
                    None,
                    None,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return user_message, None

    def complete_chat_request(
        self, request_id: UUID, thread_id: UUID, content: str
    ) -> Message:
        """助手消息与请求完成状态在同一事务提交，刷新不会看到半条回复。"""

        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,assistant_message_id FROM chat_requests WHERE request_id=?",
                (str(request_id),),
            ).fetchone()
            if not row:
                raise KeyError("聊天请求不存在")
            if row["status"] == "completed" and row["assistant_message_id"]:
                saved = db.execute(
                    "SELECT data FROM messages WHERE id=?", (row["assistant_message_id"],)
                ).fetchone()
                return self._load(Message, saved["data"])
            assistant = Message(
                thread_id=thread_id,
                role=MessageRole.ASSISTANT,
                content=content,
            )
            db.execute(
                "INSERT INTO messages VALUES(?,?,?,?)",
                (
                    str(assistant.id),
                    str(thread_id),
                    self._dump(assistant),
                    assistant.created_at.isoformat(),
                ),
            )
            db.execute(
                "UPDATE chat_requests SET status='completed',assistant_message_id=?,error=NULL WHERE request_id=?",
                (str(assistant.id), str(request_id)),
            )
        return assistant

    def fail_chat_request(self, request_id: UUID, error: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE chat_requests SET status='failed',error=? WHERE request_id=? AND status='running'",
                (error[:500], str(request_id)),
            )
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
            db.execute("DELETE FROM chat_requests WHERE thread_id=?", (key,))
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

    def get_message(self, message_id: UUID | str) -> Message | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM messages WHERE id=?", (str(message_id),)
            ).fetchone()
        return self._load(Message, row["data"]) if row else None

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
