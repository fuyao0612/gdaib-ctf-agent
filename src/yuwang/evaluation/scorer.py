"""独立评测评分器：只根据持久化 Run 事实判定，不读取 Agent 自我总结。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from yuwang.domain.models import Run, TaskSpec
from yuwang.storage import SQLiteRepository

CriterionStatus = Literal["passed", "failed", "not_executed", "configuration_error"]
ValidatorType = str


class EvaluationCriterion(BaseModel):
    """一个可由确定性评分器执行的评测维度。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,79}$")
    description: str = Field(min_length=1, max_length=500)
    weight: float = Field(default=1, gt=0, le=100)
    validator_type: str = Field(min_length=1, max_length=80)
    expected_value: str | bool | list[str] | dict[str, Any] | None = None
    required: bool = True


class CriterionResult(BaseModel):
    """单项评分结果，保留判定依据而不保存模型原始回复。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: str
    status: CriterionStatus
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    detail: str = Field(min_length=1, max_length=1000)


@dataclass(frozen=True)
class ValidationContext:
    repository: SQLiteRepository
    run: Run
    task: TaskSpec


class CriterionValidator(Protocol):
    @property
    def validator_type(self) -> str: ...

    @property
    def version(self) -> str: ...

    def validate(
        self, context: ValidationContext, criterion: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]: ...


@dataclass(frozen=True)
class FunctionValidator:
    validator_type: str
    validate_fn: Callable[[ValidationContext, EvaluationCriterion], tuple[CriterionStatus, str]]
    version: str = "1.0"

    def validate(
        self, context: ValidationContext, criterion: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        return self.validate_fn(context, criterion)


class ValidatorRegistry:
    """Small explicit registry; unsupported types remain observable configuration errors."""

    def __init__(self, validators: tuple[CriterionValidator, ...] = ()) -> None:
        self._validators = {item.validator_type: item for item in validators}

    def get(self, validator_type: str) -> CriterionValidator | None:
        return self._validators.get(validator_type)


def _expected_tools(value: object) -> set[str]:
    return (
        {str(value)}
        if isinstance(value, str)
        else {str(item) for item in value}
        if isinstance(value, list)
        else set()
    )


def _simple_validators() -> tuple[CriterionValidator, ...]:
    def run_status(
        ctx: ValidationContext, item: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        return (
            ("passed", f"Run 状态为 {ctx.run.status}")
            if str(ctx.run.status) == item.expected_value
            else ("failed", f"Run 状态为 {ctx.run.status}，期望 {item.expected_value}")
        )

    def validation_status(
        ctx: ValidationContext, item: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        return (
            ("passed", f"验证状态为 {ctx.run.validation_status}")
            if ctx.run.validation_status == item.expected_value
            else ("failed", f"验证状态为 {ctx.run.validation_status}，期望 {item.expected_value}")
        )

    def event_present(
        ctx: ValidationContext, item: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        values = {str(event.type) for event in ctx.repository.list_events(ctx.run.id)}
        return (
            ("passed", "已记录目标事件")
            if str(item.expected_value) in values
            else ("failed", "未记录目标事件")
        )

    def task_snapshot(
        ctx: ValidationContext, _: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        return (
            ("passed", "TaskSpec 快照一致")
            if ctx.repository.get_run_task(ctx.run.id) == ctx.task
            else ("failed", "TaskSpec 快照缺失或不一致")
        )

    def tool_snapshot(
        ctx: ValidationContext, _: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        saved = ctx.repository.get_run_task(ctx.run.id)
        return (
            ("passed", "已冻结工具快照")
            if saved and saved.tool_snapshots
            else ("failed", "未找到工具快照")
        )

    def agent_profile_snapshot(
        ctx: ValidationContext, _: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        return (
            ("passed", "已冻结 Agent 配置快照")
            if ctx.repository.get_run_agent_profile(ctx.run.id)
            else ("failed", "未找到 Agent 配置快照")
        )

    def provider_snapshot(
        ctx: ValidationContext, _: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        return (
            ("passed", "已冻结 Provider 快照")
            if ctx.repository.get_provider_snapshot(ctx.run.id)
            else ("not_executed", "Provider 快照不可用")
        )

    def tool_called(
        ctx: ValidationContext, item: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        actual = {
            call.tool_id or call.tool_name for call in ctx.repository.list_tool_calls(ctx.run.id)
        }
        expected = _expected_tools(item.expected_value)
        if not expected:
            return "configuration_error", "tool_called 需要 expected_value"
        return (
            ("passed", "目标工具已调用") if expected <= actual else ("failed", "未调用全部目标工具")
        )

    def artifact_sha256(
        ctx: ValidationContext, item: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        expected = str(item.expected_value or "")
        if len(expected) != 64:
            return "configuration_error", "artifact_sha256 需要 64 位 SHA-256"
        artifacts = [ctx.repository.get_artifact(value) for value in ctx.task.artifact_ids]
        return (
            ("passed", "输入 Artifact SHA-256 一致")
            if any(value and value.sha256 == expected for value in artifacts)
            else ("failed", "输入 Artifact SHA-256 不匹配")
        )

    def flag_evidence(
        ctx: ValidationContext, _: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        evidence = ctx.repository.list_evidence(ctx.run.id)
        passed = any(
            (
                value.verification_scope == "platform"
                and value.platform_validation_status == "passed"
            )
            or (
                value.rule_kind == "local_judge"
                and value.deterministic_validation_status == "passed"
            )
            for value in evidence
        )
        return (
            ("passed", "Flag 已由平台或本地 Judge 独立验证")
            if passed
            else ("failed", "Flag 格式匹配不构成正确性证明")
        )

    def result_exists(
        ctx: ValidationContext, item: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        expected = str(item.expected_value or "")
        if not expected:
            return "configuration_error", "result_exists 需要结果类型"
        return (
            ("passed", "已找到目标结果")
            if any(value.result_type == expected for value in ctx.run.results)
            else ("failed", "未找到目标结果")
        )

    def result_field_equals(
        ctx: ValidationContext, item: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        expected = item.expected_value
        if (
            not isinstance(expected, dict)
            or not isinstance(expected.get("field"), str)
            or "value" not in expected
        ):
            return "configuration_error", "result_field_equals 需要 field 和 value"
        for result in ctx.run.results:
            value: Any = result.structured_data
            for part in expected["field"].split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if value == expected["value"]:
                return "passed", "结果字段与期望一致"
        return "failed", "没有结果字段与期望一致"

    def result_contains(
        ctx: ValidationContext, item: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        expected = item.expected_value
        if (
            not isinstance(expected, dict)
            or not isinstance(expected.get("field"), str)
            or "value" not in expected
        ):
            return "configuration_error", "result_contains 需要 field 和 value"
        for result in ctx.run.results:
            value: Any = result.structured_data
            for part in expected["field"].split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if expected["value"] in value if isinstance(value, (str, list, dict)) else False:
                return "passed", "结果包含期望值"
        return "failed", "结果未包含期望值"

    def evidence_reference(
        ctx: ValidationContext, item: EvaluationCriterion
    ) -> tuple[CriterionStatus, str]:
        expected = str(item.expected_value or "")
        if not expected:
            return "configuration_error", "evidence_reference 需要证据来源引用"
        return (
            ("passed", "结果引用了目标证据")
            if any(
                expected in reference.raw_ref
                for result in ctx.run.results
                for reference in result.evidence
            )
            else ("failed", "结果未引用目标证据")
        )

    return tuple(
        FunctionValidator(name, fn)
        for name, fn in {
            "run_status": run_status,
            "validation_status": validation_status,
            "event_present": event_present,
            "task_snapshot": task_snapshot,
            "tool_snapshot": tool_snapshot,
            "agent_profile_snapshot": agent_profile_snapshot,
            "provider_snapshot": provider_snapshot,
            "tool_called": tool_called,
            "artifact_sha256": artifact_sha256,
            "flag_evidence": flag_evidence,
            "result_exists": result_exists,
            "result_field_equals": result_field_equals,
            "result_contains": result_contains,
            "evidence_reference": evidence_reference,
        }.items()
    )


class EvaluationScorer:
    """独立于 Agent 的只读评分器。

    评分只访问 SQLite 中已落库的事实，因而 Agent 的 ``finish`` 文本无法直接改变得分。
    """

    def __init__(
        self, repository: SQLiteRepository, registry: ValidatorRegistry | None = None
    ) -> None:
        self.repository = repository
        self.registry = registry or ValidatorRegistry(_simple_validators())

    def score(
        self, run: Run, task: TaskSpec, criteria: tuple[EvaluationCriterion, ...]
    ) -> tuple[CriterionResult, ...]:
        context = ValidationContext(self.repository, run, task)
        return tuple(self._score_criterion(context, item) for item in criteria)

    def _score_criterion(
        self, context: ValidationContext, criterion: EvaluationCriterion
    ) -> CriterionResult:
        validator = self.registry.get(criterion.validator_type)
        status, detail = (
            validator.validate(context, criterion)
            if validator
            else ("configuration_error", f"不支持的验证器 {criterion.validator_type}")
        )
        return CriterionResult(
            criterion_id=criterion.criterion_id,
            status=status,
            score=criterion.weight if status == "passed" else 0,
            max_score=criterion.weight,
            detail=detail,
        )


def summarize_score(results: tuple[CriterionResult, ...]) -> tuple[float, float, bool]:
    """返回总分、满分和是否满足所有必需项。"""

    total = sum(item.score for item in results)
    maximum = sum(item.max_score for item in results)
    required_ok = all(item.status == "passed" for item in results)
    return total, maximum, required_ok


__all__ = [
    "CriterionResult",
    "CriterionStatus",
    "CriterionValidator",
    "EvaluationCriterion",
    "EvaluationScorer",
    "FunctionValidator",
    "ValidationContext",
    "ValidatorRegistry",
    "ValidatorType",
    "summarize_score",
]
