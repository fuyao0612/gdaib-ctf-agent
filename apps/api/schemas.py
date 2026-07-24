"""HTTP 请求与精简响应模型。

领域对象仍位于 ``yuwang.domain``；本模块只描述浏览器传入的 JSON，防止
FastAPI 的表单细节渗入 Agent 核心。
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from yuwang.domain.models import AgentPlan, InteractionMode, ThreadMode, VerificationRule
from yuwang.verification_rules import validate_verification_rule


class ThreadCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    mode: ThreadMode = ThreadMode.NORMAL
    agent_profile_id: UUID | None = None
    plan_mode: Literal["auto", "approval"] = "auto"
    interaction_mode: InteractionMode = InteractionMode.CHAT
    provider_config_id: UUID | None = None
    skill_ids: list[UUID] = Field(default_factory=list, max_length=20)


class ThreadUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    archived: bool | None = None
    interaction_mode: InteractionMode | None = None
    provider_config_id: UUID | None = None
    acknowledge_provider_fallback: bool = False
    skill_ids: list[UUID] | None = Field(default=None, max_length=20)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    artifact_ids: list[UUID] = Field(default_factory=list)


class ChatCreate(MessageCreate):
    request_id: UUID
    provider_config_id: UUID | None = None
    retry: bool = False


class UnifiedMessageCreate(ChatCreate):
    """工作台唯一的发送契约；是否创建 Run 由服务端判断。"""


class RunCreate(BaseModel):
    provider_config_id: UUID | None = None
    authorized_targets: list[str] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=lambda: ["reference_tool_succeeded"])
    verification_rules: list[VerificationRule] = Field(default_factory=list)
    plan_mode: Literal["auto", "approval"] | None = None

    @field_validator("verification_rules")
    @classmethod
    def validate_verification_rules(
        cls, values: list[VerificationRule]
    ) -> list[VerificationRule]:
        return [validate_verification_rule(value) for value in values]


class TurnCreate(MessageCreate, RunCreate):
    """用户一次发送所需的消息与运行选项。"""


class RunInput(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    artifact_ids: list[UUID] = Field(default_factory=list)
    request_id: UUID | None = None


class ClarificationSubmit(RunInput):
    request_id: UUID
    expected_brief_version: int = Field(ge=1)


class PlanEdit(BaseModel):
    request_id: UUID
    expected_version: int = Field(ge=1)
    plan: AgentPlan
    reason: str = Field(default="用户编辑", max_length=2000)


class PlanDecision(BaseModel):
    request_id: UUID
    expected_version: int = Field(ge=1)
    reason: str = Field(default="", max_length=2000)


class ControlRequest(BaseModel):
    request_id: UUID


class GuidanceSubmit(ControlRequest):
    content: str = Field(min_length=1, max_length=10_000)
    artifact_ids: list[UUID] = Field(default_factory=list)


class MemoryToggle(BaseModel):
    enabled: bool


class ProfileCopy(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TemplatePreview(BaseModel):
    template: str = Field(min_length=1, max_length=20_000)
    values: dict[str, Any] = Field(default_factory=dict)


class AgentProfileSummary(BaseModel):
    profile_id: UUID
    version: int
    name: str
    description: str
    run_mode: ThreadMode
    completion_mode: str
    is_default: bool
