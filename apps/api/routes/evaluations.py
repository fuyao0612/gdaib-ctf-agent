"""本地评测结果的只读 API。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from apps.api.context import ApiContext
from yuwang.domain.evaluation import EvaluationRecord, EvaluationStatistics, summarize_evaluations


def create_evaluation_router(context: ApiContext) -> APIRouter:
    """挂载评测索引、明细与统计；执行仍由显式 CLI 发起以避免意外消耗。"""

    router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])

    def records(
        case_id: str | None = None,
        category: str | None = None,
        difficulty: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        status: Literal["passed", "failed", "skipped"] | None = None,
        limit: int = Query(default=200, ge=1, le=500),
    ) -> list[EvaluationRecord]:
        return context.repository.list_evaluation_records(
            case_id=case_id,
            category=category,
            difficulty=difficulty,
            provider=provider,
            model=model,
            status=status,
            limit=limit,
        )

    @router.get("", response_model=list[EvaluationRecord])
    async def list_records(
        case_id: str | None = None,
        category: str | None = None,
        difficulty: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        status: Literal["passed", "failed", "skipped"] | None = None,
        limit: int = Query(default=200, ge=1, le=500),
    ) -> list[EvaluationRecord]:
        return records(case_id, category, difficulty, provider, model, status, limit)

    @router.get("/statistics", response_model=EvaluationStatistics)
    async def statistics(
        case_id: str | None = None,
        category: str | None = None,
        difficulty: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        status: Literal["passed", "failed", "skipped"] | None = None,
        limit: int = Query(default=500, ge=1, le=500),
    ) -> EvaluationStatistics:
        return summarize_evaluations(
            records(case_id, category, difficulty, provider, model, status, limit)
        )

    @router.get("/{record_id}", response_model=EvaluationRecord)
    async def get_record(record_id: UUID) -> EvaluationRecord:
        record = context.repository.get_evaluation_record(record_id)
        if not record:
            raise HTTPException(404, "评测结果不存在")
        return record

    return router
