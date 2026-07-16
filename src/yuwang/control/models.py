"""Task Brief 与计划版本模型；所有用户和模型文本均按不可信数据保存。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from yuwang.domain.models import AgentPlan, DomainModel, utcnow


class TaskBriefSource(StrEnum):
    AGENT = "agent"
    USER_CLARIFICATION = "user_clarification"


class PlanSource(StrEnum):
    AGENT_INITIAL = "agent_initial"
    USER_EDIT = "user_edit"
    AGENT_REPLAN = "agent_replan"


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
