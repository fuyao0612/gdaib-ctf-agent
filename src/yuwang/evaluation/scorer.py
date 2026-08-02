"""独立评测评分器：只根据持久化 Run 事实判定，不读取 Agent 自我总结。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from yuwang.domain.models import Run, TaskSpec
from yuwang.storage import SQLiteRepository

CriterionStatus = Literal["passed", "failed", "not_executed"]
ValidatorType = Literal[
    "run_status",
    "validation_status",
    "event_present",
    "task_snapshot",
    "tool_snapshot",
    "agent_profile_snapshot",
    "provider_snapshot",
    "tool_called",
    "flag_evidence",
    "artifact_sha256",
]


class EvaluationCriterion(BaseModel):
    """一个可由确定性评分器执行的评测维度。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,79}$")
    description: str = Field(min_length=1, max_length=500)
    weight: float = Field(default=1, gt=0, le=100)
    validator_type: ValidatorType
    expected_value: str | bool | list[str] | None = None
    required: bool = True


class CriterionResult(BaseModel):
    """单项评分结果，保留判定依据而不保存模型原始回复。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: str
    status: CriterionStatus
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    detail: str = Field(min_length=1, max_length=1000)


class EvaluationScorer:
    """独立于 Agent 的只读评分器。

    评分只访问 SQLite 中已落库的事实，因而 Agent 的 ``finish`` 文本无法直接改变得分。
    """

    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    def score(
        self, run: Run, task: TaskSpec, criteria: tuple[EvaluationCriterion, ...]
    ) -> tuple[CriterionResult, ...]:
        return tuple(self._score_criterion(run, task, item) for item in criteria)

    def _score_criterion(
        self, run: Run, task: TaskSpec, criterion: EvaluationCriterion
    ) -> CriterionResult:
        passed, detail = self._check(run, task, criterion)
        return CriterionResult(
            criterion_id=criterion.criterion_id,
            status="passed" if passed else "failed",
            score=criterion.weight if passed else 0,
            max_score=criterion.weight,
            detail=detail,
        )

    def _check(
        self, run: Run, task: TaskSpec, criterion: EvaluationCriterion
    ) -> tuple[bool, str]:
        expected = criterion.expected_value
        if criterion.validator_type == "run_status":
            return str(run.status) == expected, f"Run 状态为 {run.status}，期望 {expected}"
        if criterion.validator_type == "validation_status":
            return (
                run.validation_status == expected,
                f"验证状态为 {run.validation_status}，期望 {expected}",
            )
        if criterion.validator_type == "event_present":
            values = {str(event.type) for event in self.repository.list_events(run.id)}
            return str(expected) in values, f"已记录事件类型：{', '.join(sorted(values)) or '无'}"
        if criterion.validator_type == "task_snapshot":
            saved = self.repository.get_run_task(run.id)
            return saved == task, "TaskSpec 快照一致" if saved == task else "TaskSpec 快照缺失或不一致"
        if criterion.validator_type == "tool_snapshot":
            saved = self.repository.get_run_task(run.id)
            passed = bool(saved and saved.tool_snapshots)
            return passed, "已冻结工具快照" if passed else "未找到工具快照"
        if criterion.validator_type == "agent_profile_snapshot":
            passed = self.repository.get_run_agent_profile(run.id) is not None
            return passed, "已冻结 Agent 配置快照" if passed else "未找到 Agent 配置快照"
        if criterion.validator_type == "provider_snapshot":
            snapshots = self.repository.get_provider_snapshot(run.id)
            passed = bool(snapshots)
            return passed, "已冻结 Provider 快照" if passed else "未找到 Provider 快照"
        if criterion.validator_type == "tool_called":
            calls = self.repository.list_tool_calls(run.id)
            expected_tools = (
                {expected}
                if isinstance(expected, str)
                else set(expected if isinstance(expected, list) else [])
            )
            actual = {call.tool_id or call.tool_name for call in calls}
            passed = expected_tools <= actual
            return (
                passed,
                f"已调用工具：{', '.join(sorted(actual)) or '无'}；期望：{', '.join(sorted(expected_tools))}",
            )
        if criterion.validator_type == "flag_evidence":
            evidence = self.repository.list_evidence(run.id)
            passed = any(
                item.deterministic_validation_status == "passed"
                and item.rule_kind == "flag_format"
                for item in evidence
            )
            return (
                passed,
                "找到通过确定性规则验证的 Flag 证据"
                if passed
                else "未找到通过确定性规则验证的 Flag 证据",
            )
        if criterion.validator_type == "artifact_sha256":
            expected_hash = str(expected or "")
            artifacts = [
                self.repository.get_artifact(artifact_id)
                for artifact_id in task.artifact_ids
            ]
            passed = any(item is not None and item.sha256 == expected_hash for item in artifacts)
            return (
                passed,
                "输入 Artifact SHA-256 与期望值一致"
                if passed
                else "输入 Artifact SHA-256 不匹配或不存在",
            )
        # ValidatorType 是受限 Literal；保留该分支以防未来扩展遗漏实现。
        return False, f"未执行：不支持的验证器 {criterion.validator_type}"


def summarize_score(results: tuple[CriterionResult, ...]) -> tuple[float, float, bool]:
    """返回总分、满分和是否满足所有必需项。"""

    total = sum(item.score for item in results)
    maximum = sum(item.max_score for item in results)
    required_ok = all(item.status == "passed" for item in results)
    return total, maximum, required_ok


__all__ = [
    "CriterionResult",
    "CriterionStatus",
    "EvaluationCriterion",
    "EvaluationScorer",
    "ValidatorType",
    "summarize_score",
]
