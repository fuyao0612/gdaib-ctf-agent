"""HTTP 请求与精简响应模型。

领域对象仍位于 ``yuwang.domain``；本模块只描述浏览器传入的 JSON，防止
FastAPI 的表单细节渗入 Agent 核心。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from yuwang.domain.models import ThreadMode, VerificationRule


class ThreadCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    mode: ThreadMode = ThreadMode.NORMAL
    agent_profile_id: UUID | None = None


class ThreadUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    archived: bool | None = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    artifact_ids: list[UUID] = Field(default_factory=list)


class RunCreate(BaseModel):
    provider_config_id: UUID | None = None
    authorized_targets: list[str] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=lambda: ["reference_tool_succeeded"])
    verification_rules: list[VerificationRule] = Field(default_factory=list)


class TurnCreate(MessageCreate, RunCreate):
    """用户一次发送所需的消息与运行选项。"""


class RunInput(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class MemoryToggle(BaseModel):
    enabled: bool


class AdminLogin(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


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
