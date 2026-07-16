"""Task Brief 与计划版本的追加式 SQLite 分区。"""

from __future__ import annotations

from uuid import UUID

from yuwang.control import PlanRevision, TaskBrief
from yuwang.storage.sqlite_common import SQLiteStore


class SQLiteControlStore(SQLiteStore):
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
