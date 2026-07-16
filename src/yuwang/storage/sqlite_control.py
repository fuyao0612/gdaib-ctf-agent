"""Task Brief 与计划版本的追加式 SQLite 分区。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import UUID

from yuwang.control import PlanRevision, TaskBrief
from yuwang.domain.models import Run, RunStatus
from yuwang.storage.sqlite_common import SQLiteStore


class SQLiteControlStore(SQLiteStore):
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
