"""Task Brief 与计划版本的追加式 SQLite 分区。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from yuwang.control import PlanRevision, RunGuidance, TaskBrief
from yuwang.domain.models import (
    Event,
    EventType,
    MemoryRecord,
    Message,
    MessageRole,
    Run,
    RunCheckpoint,
    RunStatus,
)
from yuwang.storage.sqlite_common import SQLiteStore


class SQLiteControlStore(SQLiteStore):
    def request_pause(
        self, run_id: UUID | str, request_id: UUID | str
    ) -> tuple[Run, bool]:
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM runs WHERE id=?", (str(run_id),)).fetchone()
            if not row:
                raise KeyError("run not found")
            run = self._load(Run, row["data"])
            existing = db.execute(
                "SELECT request_id,consumed_at FROM run_pause_requests WHERE run_id=?",
                (str(run_id),),
            ).fetchone()
            if existing and existing["request_id"] == str(request_id):
                return run, False
            if run.status != RunStatus.RUNNING:
                raise ValueError("只有运行中的任务可以请求暂停")
            if existing and existing["consumed_at"] is None:
                raise ValueError("暂停请求已排队")
            db.execute(
                "INSERT OR REPLACE INTO run_pause_requests VALUES(?,?,?,NULL)",
                (str(run_id), str(request_id), datetime.now(UTC).isoformat()),
            )
        return run, True

    def consume_pause_request(self, run_id: UUID | str) -> bool:
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT consumed_at FROM run_pause_requests WHERE run_id=?", (str(run_id),)
            ).fetchone()
            if not row or row["consumed_at"] is not None:
                return False
            db.execute(
                "UPDATE run_pause_requests SET consumed_at=? WHERE run_id=?",
                (datetime.now(UTC).isoformat(), str(run_id)),
            )
        return True

    def queue_guidance(
        self,
        run_id: UUID | str,
        request_id: UUID | str,
        content: str,
        artifact_ids: list[UUID] | None = None,
    ) -> tuple[RunGuidance, bool]:
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT data FROM run_guidance WHERE run_id=? AND request_id=?",
                (str(run_id), str(request_id)),
            ).fetchone()
            if row:
                existing = self._load(RunGuidance, row["data"])
                if existing.content != content or existing.artifact_ids != (artifact_ids or []):
                    raise ValueError("请求 ID 已用于不同的追加指引")
                return existing, False
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM run_guidance WHERE run_id=?",
                (str(run_id),),
            ).fetchone()["n"]
            value = RunGuidance(
                run_id=UUID(str(run_id)),
                sequence=sequence,
                content=content,
                artifact_ids=artifact_ids or [],
            )
            db.execute(
                "INSERT INTO run_guidance(run_id,sequence,request_id,data,consumed_at,created_at) VALUES(?,?,?,?,?,?)",
                (str(run_id), sequence, str(request_id), self._dump(value), None, value.created_at.isoformat()),
            )
        return value, True

    def list_guidance(self, run_id: UUID | str) -> list[RunGuidance]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM run_guidance WHERE run_id=? ORDER BY sequence", (str(run_id),)
            ).fetchall()
        return [self._load(RunGuidance, row["data"]) for row in rows]

    def find_guidance_request(
        self, thread_id: UUID | str, request_id: UUID | str
    ) -> tuple[Run, RunGuidance] | None:
        """按全局请求 ID 找到已排队的指引，用于页面重连后的 SSE 重放。"""

        with self.connect() as db:
            row = db.execute(
                "SELECT runs.data AS run_data, run_guidance.data AS guidance_data "
                "FROM run_guidance JOIN runs ON runs.id=run_guidance.run_id "
                "WHERE runs.thread_id=? AND run_guidance.request_id=?",
                (str(thread_id), str(request_id)),
            ).fetchone()
        if not row:
            return None
        return self._load(Run, row["run_data"]), self._load(
            RunGuidance, row["guidance_data"]
        )

    def consume_guidance(self, run_id: UUID | str) -> list[RunGuidance]:
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT sequence,data FROM run_guidance WHERE run_id=? AND consumed_at IS NULL ORDER BY sequence",
                (str(run_id),),
            ).fetchall()
            consumed_at = datetime.now(UTC)
            values = [
                self._load(RunGuidance, row["data"]).model_copy(update={"consumed_at": consumed_at})
                for row in rows
            ]
            for row, value in zip(rows, values, strict=True):
                db.execute(
                    "UPDATE run_guidance SET data=?,consumed_at=? WHERE run_id=? AND sequence=?",
                    (self._dump(value), consumed_at.isoformat(), str(run_id), row["sequence"]),
                )
        return values

    @staticmethod
    def _check_control_request(
        db: sqlite3.Connection,
        run_id: UUID | str,
        request_id: UUID | str,
        action: str,
        payload_hash: str,
    ) -> bool:
        row = db.execute(
            "SELECT action,payload_hash FROM run_control_requests "
            "WHERE run_id=? AND request_id=?",
            (str(run_id), str(request_id)),
        ).fetchone()
        if not row:
            return False
        if row["action"] != action or row["payload_hash"] != payload_hash:
            raise ValueError("请求 ID 已用于不同的控制操作")
        return True

    def claim_run_control(
        self,
        run_id: UUID | str,
        request_id: UUID | str,
        action: str,
        payload_hash: str,
        expected_status: RunStatus,
        expected_plan_version: int | None = None,
    ) -> tuple[Run, bool]:
        """在一个写事务中完成幂等检查、版本检查和运行态认领。"""

        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._check_control_request(db, run_id, request_id, action, payload_hash):
                row = db.execute("SELECT data FROM runs WHERE id=?", (str(run_id),)).fetchone()
                if not row:
                    raise KeyError("run not found")
                return self._load(Run, row["data"]), False
            row = db.execute("SELECT data FROM runs WHERE id=?", (str(run_id),)).fetchone()
            if not row:
                raise KeyError("run not found")
            run = self._load(Run, row["data"])
            if run.status != expected_status:
                raise ValueError(f"运行状态必须为 {expected_status}")
            if expected_plan_version is not None:
                latest = db.execute(
                    "SELECT MAX(version) AS version FROM run_plan_revisions WHERE run_id=?",
                    (str(run_id),),
                ).fetchone()["version"]
                if latest != expected_plan_version:
                    raise ValueError(f"计划版本已变化，当前为 {latest}")
            run.transition(RunStatus.RUNNING)
            db.execute(
                "UPDATE runs SET status=?,data=? WHERE id=?",
                (str(run.status), self._dump(run), str(run_id)),
            )
            db.execute(
                "INSERT INTO run_control_requests VALUES(?,?,?,?,?)",
                (
                    str(run_id),
                    str(request_id),
                    action,
                    payload_hash,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return run, True

    def commit_run_interaction(
        self,
        *,
        run_id: UUID | str,
        request_id: UUID | str,
        action: str,
        payload_hash: str,
        expected_status: RunStatus,
        message: Message,
        checkpoint_node: str,
        checkpoint_state: dict[str, Any],
        event_type: EventType,
        event_summary: str,
        event_payload: dict[str, Any],
        memory: MemoryRecord | None = None,
        expected_brief_version: int | None = None,
    ) -> tuple[Run, bool, Message]:
        """原子接收一次人工交互，并把 Run 交给恢复调度。

        认领控制请求、时间线消息、恢复检查点、审计事件和可选记忆属于同一
        用户动作。它们不能先把 Run 改成 ``running``，再分别写入；否则任一
        后续写入失败都会留下无法由同一 ``request_id`` 安全重放的半成品状态。
        调度仍在事务外执行，但事务提交后检查点和请求记录已经足以让重放再次
        尝试调度。
        """

        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._check_control_request(db, run_id, request_id, action, payload_hash):
                row = db.execute("SELECT data FROM runs WHERE id=?", (str(run_id),)).fetchone()
                message_row = db.execute(
                    "SELECT data FROM messages WHERE id=?", (str(message.id),)
                ).fetchone()
                if not row:
                    raise KeyError("run not found")
                if not message_row:
                    raise ValueError("幂等控制请求缺少消息记录")
                persisted_message = self._load(Message, message_row["data"])
                if (
                    persisted_message.thread_id != message.thread_id
                    or persisted_message.role != MessageRole.USER
                    or persisted_message.content != message.content
                    or persisted_message.artifact_ids != message.artifact_ids
                ):
                    raise ValueError("请求 ID 已用于不同的消息内容")
                return self._load(Run, row["data"]), False, persisted_message

            row = db.execute("SELECT data FROM runs WHERE id=?", (str(run_id),)).fetchone()
            if not row:
                raise KeyError("run not found")
            run = self._load(Run, row["data"])
            if run.status != expected_status:
                raise ValueError(f"运行状态必须为 {expected_status}")
            if message.thread_id != run.thread_id or message.role != MessageRole.USER:
                raise ValueError("控制消息不属于当前运行")
            if expected_brief_version is not None:
                latest = db.execute(
                    "SELECT MAX(version) AS version FROM task_briefs WHERE run_id=?",
                    (str(run_id),),
                ).fetchone()["version"]
                if latest != expected_brief_version:
                    raise ValueError(f"Task Brief 版本已变化，当前为 {latest}")

            message_row = db.execute(
                "SELECT data FROM messages WHERE id=?", (str(message.id),)
            ).fetchone()
            if message_row:
                persisted_message = self._load(Message, message_row["data"])
                if (
                    persisted_message.thread_id != message.thread_id
                    or persisted_message.role != MessageRole.USER
                    or persisted_message.content != message.content
                    or persisted_message.artifact_ids != message.artifact_ids
                ):
                    raise ValueError("请求 ID 已用于不同的消息内容")
            else:
                db.execute(
                    "INSERT INTO messages VALUES(?,?,?,?)",
                    (
                        str(message.id),
                        str(message.thread_id),
                        self._dump(message),
                        message.created_at.isoformat(),
                    ),
                )
                persisted_message = message

            if memory is not None:
                if memory.thread_id != run.thread_id or memory.source_run_id != run.id:
                    raise ValueError("补充记忆不属于当前运行")
                db.execute(
                    "INSERT OR REPLACE INTO memories VALUES(?,?,?,?,?,?)",
                    (
                        str(memory.id),
                        str(memory.thread_id),
                        memory.kind,
                        int(memory.enabled),
                        self._dump(memory),
                        memory.created_at.isoformat(),
                    ),
                )

            elapsed = float(checkpoint_state.get("elapsed_seconds", 0.0))
            checkpoint_sequence = int(
                db.execute(
                    "SELECT COALESCE(MAX(checkpoint_sequence),0)+1 AS n "
                    "FROM run_checkpoints WHERE run_id=?",
                    (str(run_id),),
                ).fetchone()["n"]
            )
            checkpoint = RunCheckpoint(
                run_id=UUID(str(run_id)),
                checkpoint_sequence=checkpoint_sequence,
                node=checkpoint_node,
                state=checkpoint_state,
                elapsed_seconds=elapsed,
            )
            event_sequence = int(
                db.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM events WHERE run_id=?",
                    (str(run_id),),
                ).fetchone()["n"]
            )
            event = Event(
                run_id=UUID(str(run_id)),
                sequence=event_sequence,
                type=event_type,
                summary=event_summary,
                payload=event_payload,
            )
            run.transition(RunStatus.RUNNING)

            db.execute(
                "INSERT INTO run_checkpoints(run_id,checkpoint_sequence,node,state_schema_version,elapsed_seconds,data) "
                "VALUES(?,?,?,?,?,?)",
                (
                    str(run_id),
                    checkpoint_sequence,
                    checkpoint.node,
                    checkpoint.state_schema_version,
                    elapsed,
                    checkpoint.model_dump_json(),
                ),
            )
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?)",
                (
                    str(event.event_id),
                    str(run_id),
                    event.sequence,
                    str(event.type),
                    self._dump(event),
                ),
            )
            db.execute(
                "UPDATE runs SET status=?,data=? WHERE id=?",
                (str(run.status), self._dump(run), str(run_id)),
            )
            db.execute(
                "INSERT INTO run_control_requests VALUES(?,?,?,?,?)",
                (
                    str(run_id),
                    str(request_id),
                    action,
                    payload_hash,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return run, True, persisted_message

    def find_control_request(
        self, thread_id: UUID | str, request_id: UUID | str
    ) -> tuple[Run, str, str] | None:
        """读取已接受的补充/澄清控制请求，不重新改变 Run 状态。"""

        with self.connect() as db:
            row = db.execute(
                "SELECT runs.data AS run_data, run_control_requests.action, "
                "run_control_requests.payload_hash "
                "FROM run_control_requests JOIN runs ON runs.id=run_control_requests.run_id "
                "WHERE runs.thread_id=? AND run_control_requests.request_id=? "
                "ORDER BY run_control_requests.created_at DESC LIMIT 1",
                (str(thread_id), str(request_id)),
            ).fetchone()
        if not row:
            return None
        return self._load(Run, row["run_data"]), row["action"], row["payload_hash"]

    def save_user_plan_revision(
        self,
        value: PlanRevision,
        request_id: UUID | str,
        payload_hash: str,
    ) -> tuple[PlanRevision, bool]:
        """用户编辑按计划版本串行追加；重复请求返回已保存版本。"""

        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._check_control_request(
                db, value.run_id, request_id, "plan_edit", payload_hash
            ):
                row = db.execute(
                    "SELECT data FROM run_plan_revisions WHERE run_id=? AND version=?",
                    (str(value.run_id), value.version),
                ).fetchone()
                if not row:
                    raise ValueError("幂等计划版本缺失")
                return self._load(PlanRevision, row["data"]), False
            latest = db.execute(
                "SELECT MAX(version) AS version FROM run_plan_revisions WHERE run_id=?",
                (str(value.run_id),),
            ).fetchone()["version"]
            if latest != value.based_on_version:
                raise ValueError(f"计划版本已变化，当前为 {latest}")
            db.execute(
                "INSERT INTO run_plan_revisions(run_id,version,source,data,created_at) "
                "VALUES(?,?,?,?,?)",
                (
                    str(value.run_id),
                    value.version,
                    str(value.source),
                    self._dump(value),
                    value.created_at.isoformat(),
                ),
            )
            db.execute(
                "INSERT INTO run_control_requests VALUES(?,?,?,?,?)",
                (
                    str(value.run_id),
                    str(request_id),
                    "plan_edit",
                    payload_hash,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return value, True
    def save_task_brief(self, value: TaskBrief) -> TaskBrief:
        previous = self.latest_task_brief(value.run_id)
        expected_version = 1 if previous is None else previous.version + 1
        if value.version != expected_version:
            raise ValueError(f"Task Brief 版本必须为 {expected_version}")
        if previous and previous.original_request != value.original_request:
            raise ValueError("Task Brief 原始要求不可修改")
        with self.connect() as db:
            db.execute(
                "INSERT INTO task_briefs(run_id,version,data,created_at) VALUES(?,?,?,?)",
                (str(value.run_id), value.version, self._dump(value), value.created_at.isoformat()),
            )
        return value

    def list_task_briefs(self, run_id: UUID | str) -> list[TaskBrief]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM task_briefs WHERE run_id=? ORDER BY version", (str(run_id),)
            ).fetchall()
        return [self._load(TaskBrief, row["data"]) for row in rows]

    def latest_task_brief(self, run_id: UUID | str) -> TaskBrief | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM task_briefs WHERE run_id=? ORDER BY version DESC LIMIT 1",
                (str(run_id),),
            ).fetchone()
        return self._load(TaskBrief, row["data"]) if row else None

    def save_plan_revision(self, value: PlanRevision) -> PlanRevision:
        previous = self.latest_plan_revision(value.run_id)
        expected_version = 1 if previous is None else previous.version + 1
        if value.version != expected_version:
            raise ValueError(f"计划版本必须为 {expected_version}")
        with self.connect() as db:
            db.execute(
                "INSERT INTO run_plan_revisions(run_id,version,source,data,created_at) VALUES(?,?,?,?,?)",
                (
                    str(value.run_id),
                    value.version,
                    str(value.source),
                    self._dump(value),
                    value.created_at.isoformat(),
                ),
            )
        return value

    def list_plan_revisions(self, run_id: UUID | str) -> list[PlanRevision]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM run_plan_revisions WHERE run_id=? ORDER BY version",
                (str(run_id),),
            ).fetchall()
        return [self._load(PlanRevision, row["data"]) for row in rows]

    def latest_plan_revision(self, run_id: UUID | str) -> PlanRevision | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM run_plan_revisions WHERE run_id=? ORDER BY version DESC LIMIT 1",
                (str(run_id),),
            ).fetchone()
        return self._load(PlanRevision, row["data"]) if row else None
