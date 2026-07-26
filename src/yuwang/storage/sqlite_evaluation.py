"""评测结果存储：保留 Run 引用并提供受限条件查询。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from yuwang.domain.evaluation import EvaluationRecord
from yuwang.storage.sqlite_common import SQLiteStore


class SQLiteEvaluationStore(SQLiteStore):
    """评测结果不复制轨迹，轨迹始终通过对应 Run 的事件和报告读取。"""

    def save_evaluation_record(self, value: EvaluationRecord) -> EvaluationRecord:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO evaluation_results "
                "(id,case_id,category,difficulty,provider,model,status,run_id,finished_at,data) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(value.id),
                    value.case_id,
                    value.category,
                    value.difficulty,
                    value.provider,
                    value.model,
                    value.status,
                    str(value.run_id) if value.run_id else None,
                    value.finished_at.isoformat(),
                    value.model_dump_json(),
                ),
            )
        return value

    def get_evaluation_record(self, record_id: UUID | str) -> EvaluationRecord | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM evaluation_results WHERE id=?", (str(record_id),)
            ).fetchone()
        return EvaluationRecord.model_validate_json(row["data"]) if row else None

    def list_evaluation_records(
        self,
        *,
        case_id: str | None = None,
        category: str | None = None,
        difficulty: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        status: Literal["passed", "failed", "skipped"] | None = None,
        limit: int = 200,
    ) -> list[EvaluationRecord]:
        predicates: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("case_id", case_id),
            ("category", category),
            ("difficulty", difficulty),
            ("provider", provider),
            ("model", model),
            ("status", status),
        ):
            if value is not None:
                predicates.append(f"{column}=?")
                parameters.append(value)
        query = "SELECT data FROM evaluation_results"
        if predicates:
            query += " WHERE " + " AND ".join(predicates)
        query += " ORDER BY finished_at DESC LIMIT ?"
        parameters.append(limit)
        with self.connect() as db:
            rows = db.execute(query, parameters).fetchall()
        return [EvaluationRecord.model_validate_json(row["data"]) for row in rows]
