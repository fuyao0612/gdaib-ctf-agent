"""评测结果的领域模型，与存储和 HTTP 适配层解耦。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from statistics import median
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .models import utcnow

EvaluationStatus = Literal["passed", "failed", "skipped"]
FailureCategory = Literal[
    "provider_failure",
    "tool_unavailable",
    "tool_failure",
    "step_limit",
    "time_limit",
    "budget_limit",
    "context_failure",
    "wrong_flag",
    "agent_abandoned",
    "assertion_failed",
    "provider_unavailable",
    "internal_error",
    "configuration_error",
]


class EvaluationRecord(BaseModel):
    """一次评测尝试的只读审计摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    case_id: str = Field(min_length=1, max_length=80)
    case_version: str = Field(default="1.0", min_length=1, max_length=20)
    scenario: str = Field(default="general", min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    difficulty: str = Field(min_length=1, max_length=40)
    provider: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=160)
    attempt: int = Field(ge=1)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime = Field(default_factory=utcnow)
    duration_ms: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    provider_requests: int = Field(default=0, ge=0)
    tool_calls: int = Field(ge=0)
    tool_failures: int = Field(default=0, ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost: float = Field(ge=0)
    success: bool
    status: EvaluationStatus
    execution_status: str = Field(default="unknown", min_length=1, max_length=40)
    validation_status: str = Field(default="pending", min_length=1, max_length=40)
    submitted_flag: str | None = Field(default=None, max_length=500)
    flag_verified: bool = False
    finish_reason: str = Field(min_length=1, max_length=500)
    failure_category: FailureCategory | None = None
    run_id: UUID | None = None
    trace_path: str | None = Field(default=None, max_length=300)
    report_path: str | None = Field(default=None, max_length=300)
    score: float = Field(default=0, ge=0)
    max_score: float = Field(default=0, ge=0)
    criterion_results: list[dict[str, object]] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    replans: int = Field(default=0, ge=0)
    manual_interventions: int = Field(default=0, ge=0)
    context_compressions: int = Field(default=0, ge=0)


class EvaluationStatistics(BaseModel):
    """结果筛选后的真实聚合，不将 skipped 计入成功率分母。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    pass_at_1: float = Field(ge=0, le=1)
    pass_at_3: float = Field(ge=0, le=1)
    average_duration_ms: float = Field(ge=0)
    median_duration_ms: float = Field(ge=0)
    average_tokens: float = Field(ge=0)
    average_cost: float = Field(ge=0)
    average_tool_calls: float = Field(ge=0)
    average_replans: float = Field(ge=0)
    average_manual_interventions: float = Field(ge=0)
    failure_categories: dict[str, int]


def summarize_evaluations(records: list[EvaluationRecord]) -> EvaluationStatistics:
    """计算筛选结果的统计，空集合保持稳定的零值输出。"""

    counts = Counter(record.status for record in records)
    executed = [record for record in records if record.status != "skipped"]
    failures = Counter(
        record.failure_category for record in records if record.failure_category is not None
    )
    divisor = len(executed)
    by_case: dict[str, list[EvaluationRecord]] = {}
    for record in executed:
        by_case.setdefault(record.case_id, []).append(record)
    first_attempts = [
        min(values, key=lambda value: value.attempt)
        for values in by_case.values()
        if any(value.attempt == 1 for value in values)
    ]
    pass_at_three_cases = [
        any(value.success for value in values if value.attempt <= 3)
        for values in by_case.values()
        if any(value.attempt <= 3 for value in values)
    ]
    return EvaluationStatistics(
        total=len(records),
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        success_rate=counts["passed"] / divisor if divisor else 0,
        pass_at_1=(sum(value.success for value in first_attempts) / len(first_attempts) if first_attempts else 0),
        pass_at_3=(sum(pass_at_three_cases) / len(pass_at_three_cases) if pass_at_three_cases else 0),
        average_duration_ms=(sum(record.duration_ms for record in executed) / divisor if divisor else 0),
        median_duration_ms=(float(median(record.duration_ms for record in executed)) if executed else 0),
        average_tokens=(
            sum(record.input_tokens + record.output_tokens for record in executed) / divisor
            if divisor
            else 0
        ),
        average_cost=(sum(record.estimated_cost for record in executed) / divisor if divisor else 0),
        average_tool_calls=(sum(record.tool_calls for record in executed) / divisor if divisor else 0),
        average_replans=(sum(record.replans for record in executed) / divisor if divisor else 0),
        average_manual_interventions=(
            sum(record.manual_interventions for record in executed) / divisor if divisor else 0
        ),
        failure_categories=dict(sorted(failures.items())),
    )


__all__ = [
    "EvaluationRecord",
    "EvaluationStatistics",
    "EvaluationStatus",
    "FailureCategory",
    "summarize_evaluations",
]
