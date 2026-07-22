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

    def _persist_user_message(
        self, db: sqlite3.Connection, thread_id: UUID, message: Message
    ) -> Message:
        """在控制事务中复用或写入同一条用户时间线消息。"""

        if message.thread_id != thread_id or message.role != MessageRole.USER:
            raise ValueError("控制消息不属于当前运行")
        row = db.execute("SELECT data FROM messages WHERE id=?", (str(message.id),)).fetchone()
        if row:
            persisted = self._load(Message, row["data"])
            if (
                persisted.thread_id != message.thread_id
                or persisted.role != MessageRole.USER
                or persisted.content != message.content
                or persisted.artifact_ids != message.artifact_ids
            ):
                raise ValueError("请求 ID 已用于不同的消息内容")
            return persisted
        db.execute(
            "INSERT INTO messages VALUES(?,?,?,?)",
            (
                str(message.id),
                str(message.thread_id),
                self._dump(message),
                message.created_at.isoformat(),
            ),
        )
        return message

    def commit_guidance_interaction(
        self,
        *,
        run_id: UUID | str,
        message: Message,
    ) -> tuple[Run, RunGuidance, bool, Message]:
        """原子提交可接受状态校验、时间线消息、指引和公开排队事件。

        指引在安全检查点才会被 Agent 消费；因此不能把消息和指引分开写入，
        否则 Run 恰好终止时会留下界面永远显示“已排队”的半成品记录。
        """

        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run_row = db.execute("SELECT data FROM runs WHERE id=?", (str(run_id),)).fetchone()
            if not run_row:
                raise KeyError("run not found")
            run = self._load(Run, run_row["data"])
            guidance_row = db.execute(
                "SELECT data FROM run_guidance WHERE run_id=? AND request_id=?",
                (str(run_id), str(message.id)),
            ).fetchone()
            if guidance_row:
                guidance = self._load(RunGuidance, guidance_row["data"])
                if (
                    guidance.content != message.content
                    or guidance.artifact_ids != message.artifact_ids
                ):
                    raise ValueError("请求 ID 已用于不同的追加指引")
                message_row = db.execute(
                    "SELECT data FROM messages WHERE id=?", (str(message.id),)
                ).fetchone()
                if not message_row:
                    raise ValueError("幂等追加指引缺少时间线消息")
                persisted_message = self._persist_user_message(db, run.thread_id, message)
                return run, guidance, False, persisted_message

            if run.status not in {
                RunStatus.QUEUED,
                RunStatus.RUNNING,
                RunStatus.PAUSED,
                RunStatus.WAITING_APPROVAL,
            }:
                raise ValueError("当前状态不能追加指引")
            # 停止请求比终态更早抵达时，状态仍可能是 running。此时继续接收
            # 新指引只会让它在收尾时失去应用机会，因此必须在同一事务中拒绝。
            if run.stop_requested:
                raise ValueError("任务已收到停止请求，不能追加指引")
            persisted_message = self._persist_user_message(db, run.thread_id, message)
            sequence = int(
                db.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM run_guidance WHERE run_id=?",
                    (str(run_id),),
                ).fetchone()["n"]
            )
            guidance = RunGuidance(
                run_id=UUID(str(run_id)),
                sequence=sequence,
                content=message.content,
                artifact_ids=message.artifact_ids,
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
                type=EventType.GUIDANCE_QUEUED,
                summary="追加指引已排队",
                payload={
                    "sequence": guidance.sequence,
                    "content_length": len(message.content),
                    "artifact_count": len(message.artifact_ids),
                },
            )
            db.execute(
                "INSERT INTO run_guidance(run_id,sequence,request_id,data,consumed_at,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    str(run_id),
                    sequence,
                    str(message.id),
                    self._dump(guidance),
                    None,
                    guidance.created_at.isoformat(),
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
        return run, guidance, True, persisted_message

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

    def list_pending_guidance(self, run_id: UUID | str) -> list[RunGuidance]:
        """只读取尚未结算的指引，供安全检查点在事务中认领。"""

        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM run_guidance WHERE run_id=? AND consumed_at IS NULL "
                "ORDER BY sequence",
                (str(run_id),),
            ).fetchall()
        return [self._load(RunGuidance, row["data"]) for row in rows]

    def _settle_terminal_guidance(
        self, db: sqlite3.Connection, run: Run
    ) -> list[RunGuidance]:
        """在写入终态的同一事务中结算最后一个检查点之后到达的指引。

        这些记录已真实写入时间线，不能删除；但 Agent 已无安全检查点可应用。
        同时写入明确审计和 ``discarded_at``，避免 UI 长期停留在“已排队”或
        把未应用的内容误报成已应用。
        """

        rows = db.execute(
            "SELECT sequence,data FROM run_guidance WHERE run_id=? AND consumed_at IS NULL "
            "ORDER BY sequence",
            (str(run.id),),
        ).fetchall()
        if not rows:
            return []
        settled_at = datetime.now(UTC)
        event_sequence = int(
            db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM events WHERE run_id=?",
                (str(run.id),),
            ).fetchone()["n"]
        )
        values: list[RunGuidance] = []
        for row in rows:
            value = self._load(RunGuidance, row["data"]).model_copy(
                update={"consumed_at": settled_at, "discarded_at": settled_at}
            )
            db.execute(
                "UPDATE run_guidance SET data=?,consumed_at=? WHERE run_id=? AND sequence=?",
                (self._dump(value), settled_at.isoformat(), str(run.id), row["sequence"]),
            )
            event = Event(
                run_id=run.id,
                sequence=event_sequence,
                type=EventType.GUIDANCE_SKIPPED,
                summary="任务结束，追加指引未应用",
                payload={
                    "sequence": value.sequence,
                    "terminal_status": str(run.status),
                },
            )
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?)",
                (
                    str(event.event_id),
                    str(run.id),
                    event.sequence,
                    str(event.type),
                    self._dump(event),
                ),
            )
            event_sequence += 1
            values.append(value)
        return values

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

    def commit_guidance_checkpoint(
        self,
        *,
        run_id: UUID | str,
        node: str,
        state: dict[str, Any],
        guidance: list[RunGuidance],
    ) -> None:
        """原子保存“已应用指引、公开事件和恢复检查点”。

        先标记 ``consumed_at`` 再单独保存检查点会在进程异常时丢失指引；这里将
        三者放入一个 SQLite 写事务，重启时要么完整恢复新状态，要么仍可再次应用。
        """

        if not guidance:
            raise ValueError("没有可提交的追加指引")
        expected = {item.sequence for item in guidance}
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT sequence,data FROM run_guidance WHERE run_id=? AND consumed_at IS NULL "
                "ORDER BY sequence",
                (str(run_id),),
            ).fetchall()
            pending = {
                int(row["sequence"]): self._load(RunGuidance, row["data"]) for row in rows
            }
            if not expected.issubset(pending):
                raise ValueError("追加指引已被其他安全检查点结算")

            consumed_at = datetime.now(UTC)
            event_sequence = int(
                db.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM events WHERE run_id=?",
                    (str(run_id),),
                ).fetchone()["n"]
            )
            for sequence in sorted(expected):
                value = pending[sequence].model_copy(update={"consumed_at": consumed_at})
                db.execute(
                    "UPDATE run_guidance SET data=?,consumed_at=? WHERE run_id=? AND sequence=?",
                    (self._dump(value), consumed_at.isoformat(), str(run_id), sequence),
                )
                event = Event(
                    run_id=UUID(str(run_id)),
                    sequence=event_sequence,
                    type=EventType.GUIDANCE_APPLIED,
                    summary="追加指引已在安全检查点应用",
                    payload={"sequence": sequence},
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
                event_sequence += 1

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
                node=node,
                state=state,
                elapsed_seconds=float(state.get("elapsed_seconds", 0.0)),
            )
            db.execute(
                "INSERT INTO run_checkpoints(run_id,checkpoint_sequence,node,state_schema_version,"
                "elapsed_seconds,data) VALUES(?,?,?,?,?,?)",
                (
                    str(run_id),
                    checkpoint.checkpoint_sequence,
                    checkpoint.node,
                    checkpoint.state_schema_version,
                    checkpoint.elapsed_seconds,
                    self._dump(checkpoint),
                ),
            )

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
            if expected_brief_version is not None:
                latest = db.execute(
                    "SELECT MAX(version) AS version FROM task_briefs WHERE run_id=?",
                    (str(run_id),),
                ).fetchone()["version"]
                if latest != expected_brief_version:
                    raise ValueError(f"Task Brief 版本已变化，当前为 {latest}")

            persisted_message = self._persist_user_message(db, run.thread_id, message)

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
