"""Task Brief 与计划版本模型；所有用户和模型文本均按不可信数据保存。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yuwang.domain.models import (
    AgentAction,
    AgentPlan,
    DomainModel,
    EvidenceCandidate,
    Observation,
    PlanStep,
    utcnow,
)


class TaskBriefSource(StrEnum):
    AGENT = "agent"
    USER_CLARIFICATION = "user_clarification"


class PlanSource(StrEnum):
    AGENT_INITIAL = "agent_initial"
    USER_EDIT = "user_edit"
    AGENT_REPLAN = "agent_replan"


class TaskBriefDraft(BaseModel):
    """模型只生成公开业务字段，版本、来源和运行归属由服务端补齐。"""

    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=10_000)
    authorized_scope: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    success_criteria: list[str] = Field(default_factory=list, max_length=100)
    expected_output: str = Field(default="", max_length=10_000)
    known_information: list[str] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    risks: list[str] = Field(default_factory=list, max_length=100)
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_questions_when_needed(self) -> TaskBriefDraft:
        if self.needs_clarification and not self.clarification_questions:
            raise ValueError("需要澄清时必须提供至少一个公开问题")
        return self


class TaskBrief(DomainModel):
    """可恢复的公开任务说明；不保存模型隐藏思维链。"""

    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    version: int = Field(ge=1)
    original_request: str = Field(min_length=1, max_length=100_000)
    goal: str = Field(min_length=1, max_length=10_000)
    authorized_scope: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    success_criteria: list[str] = Field(default_factory=list, max_length=100)
    expected_output: str = Field(default="", max_length=10_000)
    known_information: list[str] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    risks: list[str] = Field(default_factory=list, max_length=100)
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list, max_length=20)
    source: TaskBriefSource = TaskBriefSource.AGENT
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def require_questions_when_clarification_needed(self) -> TaskBrief:
        if self.needs_clarification and not self.clarification_questions:
            raise ValueError("需要澄清时必须提供至少一个公开问题")
        return self


class PlanStepDraft(BaseModel):
    """模型输出的公开步骤字段；服务端仍会校验工具和授权。"""

    model_config = ConfigDict(extra="forbid")
    step_id: str = Field(pattern=r"^step-[1-9][0-9]{0,2}$")
    goal: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=600)
    expected_result: str = Field(min_length=1, max_length=1000)
    verification_method: str = Field(min_length=1, max_length=1000)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    risk: Literal["low", "medium", "high"] = "low"


class AgentPlanDraft(BaseModel):
    """模型仅生成稳定的计划骨架，完整计划字段由服务端确定性补齐。"""

    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=500)
    steps: list[str | PlanStepDraft] = Field(min_length=1, max_length=30)

    def to_agent_plan(self) -> AgentPlan:
        """保持计划展示完整，但不把安全执行决策交给模型的自由文本。"""

        success_approach = "使用当前 Run 快照中已启用且经策略允许的工具收集证据，并按任务规则核对结果。"
        drafts = [
            value
            if isinstance(value, PlanStepDraft)
            else PlanStepDraft(
                step_id=f"step-{index}",
                goal=value,
                reason="按任务目标推进并收集公开证据。",
                expected_result=f"完成：{value}",
                verification_method=success_approach,
            )
            for index, value in enumerate(self.steps, 1)
        ]
        details = [PlanStep(**value.model_dump()) for value in drafts]
        return AgentPlan(
            summary=self.summary,
            steps=[value.goal for value in details],
            success_approach=success_approach,
            risks=["工具、目标范围和参数仍须通过 Run 快照与 PolicyEngine 校验。"],
            expected_results=[value.expected_result for value in details],
            verification_methods=[value.verification_method for value in details],
            step_details=details,
        )


AgentPlanDraft.model_rebuild()


class AgentActionDraft(BaseModel):
    """模型只提供动作展示字段，候选证据引用由服务端绑定真实观察记录。"""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["call_tool", "replan", "finish", "fail", "request_input"]
    summary: str = Field(min_length=1, max_length=10_000)
    reason: str = Field(min_length=1, max_length=600)
    tool_name: str | None = Field(default=None, min_length=1, max_length=500)
    tool_input: dict[str, Any] = Field(default_factory=dict)
    candidate: str | dict[str, Any] | None = None
    updated_plan: list[str] = Field(default_factory=list, max_length=30)
    answer: str | None = Field(default=None, max_length=100_000)
    structured_output: dict[str, Any] | None = None

    def to_agent_action(self, observations: list[Observation]) -> AgentAction:
        value = self.candidate if isinstance(self.candidate, str) else (
            self.candidate.get("value") if isinstance(self.candidate, dict) else None
        )
        candidate = self._bind_candidate(value, observations) if isinstance(value, str) else None
        if candidate is None and self.kind == "finish":
            candidate = self._latest_verified_flag_candidate(observations)
        return AgentAction(
            kind=self.kind,
            summary=self.summary,
            action_reason=self.reason,
            tool_name=self.tool_name,
            tool_input=self.tool_input,
            candidate=candidate,
            updated_plan=self.updated_plan,
            answer=self.answer,
            structured_output=self.structured_output,
        )

    @staticmethod
    def _bind_candidate(value: str, observations: list[Observation]) -> EvidenceCandidate | None:
        for observation in reversed(observations):
            if not observation.success:
                continue
            for key, item in observation.output.items():
                if str(item) == value:
                    return EvidenceCandidate(
                        value=value,
                        source_call_id=observation.call_id,
                        location=f"/{str(key).replace('~', '~0').replace('/', '~1')}",
                    )
        return None

    @staticmethod
    def _latest_verified_flag_candidate(
        observations: list[Observation],
    ) -> EvidenceCandidate | None:
        """收尾遗漏候选字段时，只复用刚由专用工具产生的真实格式验证证据。"""

        for observation in reversed(observations):
            if (
                observation.success
                and observation.tool_name == "ctf.flag_candidate_verify"
                and observation.output.get("validation_status") == "format_matched"
                and isinstance(observation.output.get("candidate"), str)
            ):
                return EvidenceCandidate(
                    value=observation.output["candidate"],
                    source_call_id=observation.call_id,
                    location="/candidate",
                )
        return None


class PlanRevision(DomainModel):
    """AgentPlan 的追加式版本包装，不复制计划字段。"""

    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    version: int = Field(ge=1)
    plan: AgentPlan
    source: PlanSource
    change_reason: str = Field(default="", max_length=2000)
    based_on_version: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def validate_parent_version(self) -> PlanRevision:
        expected = None if self.version == 1 else self.version - 1
        if self.based_on_version != expected:
            raise ValueError("计划版本必须指向直接前一版本")
        return self


class RunGuidance(DomainModel):
    """运行中按提交顺序持久化的用户指引。

    ``consumed_at`` 表示该记录已经结算、不会再次进入 Agent；正常在安全检查点
    应用时只设置它。若任务已进入终态而没有剩余检查点，``discarded_at`` 会同时
    记录原因，界面必须明确显示“未应用”，不能把它伪装成已应用。
    """

    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    sequence: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=10_000)
    artifact_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    consumed_at: datetime | None = None
    discarded_at: datetime | None = None
