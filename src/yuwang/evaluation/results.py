"""评测层对领域结果模型的兼容导出。"""

from yuwang.domain.evaluation import (
    EvaluationRecord,
    EvaluationStatistics,
    EvaluationStatus,
    FailureCategory,
    summarize_evaluations,
)

__all__ = [
    "EvaluationRecord",
    "EvaluationStatistics",
    "EvaluationStatus",
    "FailureCategory",
    "summarize_evaluations",
]
