"""跨层共享的领域模型与状态转换约束，不包含基础设施细节。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)
    schema_version: str = "1.0"


class ThreadMode(StrEnum):
    NORMAL = "normal"
    COMPETITION = "competition"


class InteractionMode(StrEnum):
    CHAT = "chat"
    AGENT = "agent"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_CLARIFICATION = "waiting_clarification"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


ValidationStatus = Literal["pending", "unverified", "partial", "validated", "failed"]
EvidenceLevel = Literal["none", "model", "structured", "external"]


ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.WAITING_INPUT,
    RunStatus.WAITING_CLARIFICATION,
    RunStatus.WAITING_APPROVAL,
    RunStatus.PAUSED,
}


class MessageRole(StrEnum):
    USER = "user"
    AGENT = "agent"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class EventType(StrEnum):
    RUN_STARTED = "run_started"
    STATUS_UPDATE = "status_update"
    PLAN_UPDATED = "plan_updated"
    POLICY_CHECKED = "policy_checked"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    REPLANNED = "replanned"
    WARNING = "warning"
    ARTIFACT_CREATED = "artifact_created"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_STOPPED = "run_stopped"
    RUN_WAITING_INPUT = "run_waiting_input"
    INPUT_RECEIVED = "input_received"
    CONTEXT_TRUNCATED = "context_truncated"
    TASK_BRIEF_CREATED = "task_brief_created"
    CLARIFICATION_REQUESTED = "clarification_requested"
    CLARIFICATION_RECEIVED = "clarification_received"
    PLAN_CREATED = "plan_created"
    PLAN_APPROVAL_REQUESTED = "plan_approval_requested"
    PLAN_EDITED = "plan_edited"
    PLAN_APPROVED = "plan_approved"
    PLAN_REJECTED = "plan_rejected"
    GUIDANCE_QUEUED = "guidance_queued"
    GUIDANCE_APPLIED = "guidance_applied"
    GUIDANCE_SKIPPED = "guidance_skipped"
    PAUSE_REQUESTED = "pause_requested"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_steps: int = Field(20, ge=1, le=100)
    max_model_calls: int = Field(8, ge=1, le=50)
    max_tool_calls: int = Field(8, ge=1, le=50)
    max_tokens: int = Field(8000, ge=1, le=200_000)
    max_model_cost: float = Field(10.0, ge=0, le=100_000)
    max_duration_seconds: float = Field(120, gt=0, le=3600)
    step_timeout_seconds: float = Field(15, gt=0, le=300)


class Thread(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=160)
    mode: ThreadMode = ThreadMode.NORMAL
    # 旧数据缺少该字段时继续按 Agent 任务恢复；新建会话由 API 显式默认成 chat。
    interaction_mode: InteractionMode = InteractionMode.AGENT
    # 对话级模型选择独立于全局默认值。Run 启动时再把实际 Provider 固化为快照，
    # 因此用户切换这里的值绝不会改变已经运行中的任务。
    provider_config_id: UUID | None = None
    # 已失效的会话选择被安全回退时保留一次性提示，前端确认展示后会清空它。
    provider_fallback_notice: str | None = None
    agent_profile_id: UUID | None = None
    agent_profile_version: int | None = Field(default=None, ge=1)
    plan_mode: Literal["auto", "approval"] = "auto"
    archived: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Message(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    role: MessageRole
    content: str = Field(min_length=1, max_length=100_000)
    artifact_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class Run(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    status: RunStatus = RunStatus.QUEUED
    provider: str = "unconfigured"
    provider_config_id: UUID | None = None
    agent_profile_id: UUID | None = None
    agent_profile_version: int | None = Field(default=None, ge=1)
    plan_mode: Literal["auto", "approval"] = "auto"
    attempt: int = Field(1, ge=1)
    stop_requested: bool = False
    # 统一输入用这个 ID 重放已完成的停止响应，刷新或断线重发不会把“停止”
    # 误判为一条新的聊天消息。旧 Run 没有该字段时保持 None。
    stop_request_id: UUID | None = None
    error: str | None = None
    completion_mode: str = "evidence"
    # status 描述执行生命周期；验证结论与证据强度必须独立展示，不能由完成状态推断。
    validation_status: ValidationStatus = "pending"
    evidence_level: EvidenceLevel = "none"
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def transition(self, target: RunStatus, error: str | None = None) -> None:
        allowed = {
            RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.STOPPED},
            RunStatus.RUNNING: {
                RunStatus.WAITING_INPUT,
                RunStatus.WAITING_CLARIFICATION,
                RunStatus.WAITING_APPROVAL,
                RunStatus.PAUSED,
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.STOPPED,
            },
            RunStatus.WAITING_INPUT: {
                RunStatus.RUNNING,
                RunStatus.FAILED,
                RunStatus.STOPPED,
            },
            RunStatus.WAITING_CLARIFICATION: {
                RunStatus.RUNNING,
                RunStatus.FAILED,
                RunStatus.STOPPED,
            },
            RunStatus.WAITING_APPROVAL: {
                RunStatus.RUNNING,
                RunStatus.FAILED,
                RunStatus.STOPPED,
            },
            RunStatus.PAUSED: {
                RunStatus.RUNNING,
                RunStatus.FAILED,
                RunStatus.STOPPED,
            },
            RunStatus.COMPLETED: set(),
            RunStatus.FAILED: set(),
            RunStatus.STOPPED: set(),
        }
        current = RunStatus(self.status)
        if target not in allowed[current]:
            raise ValueError(f"illegal run transition: {current} -> {target}")
        self.status = target
        self.error = error
        if target == RunStatus.RUNNING:
            self.started_at = utcnow()
        if target in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.STOPPED}:
            self.finished_at = utcnow()


class Event(DomainModel):
    event_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    sequence: int = Field(ge=1)
    type: EventType
    timestamp: datetime = Field(default_factory=utcnow)
    summary: str = Field(min_length=1, max_length=500)
    payload: dict[str, Any] = Field(default_factory=dict)


class Artifact(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    run_id: UUID | None = None
    filename: str = Field(min_length=1, max_length=255)
    kind: str = Field(min_length=1, max_length=80)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)
    mime_type: str = Field(min_length=1, max_length=200)
    storage_ref: str = Field(min_length=1, max_length=500)
    created_at: datetime = Field(default_factory=utcnow)

    @field_validator("storage_ref")
    @classmethod
    def reject_absolute_storage_ref(cls, value: str) -> str:
        if value.startswith(("/", "\\")) or ":\\" in value or ":/" in value:
            raise ValueError("storage_ref must be an opaque relative reference")
        return value


class TaskSpec(DomainModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)
    body: str = Field(min_length=1, max_length=100_000)
    # TaskSpec 是不可变运行快照；保留来源消息可让统一入口安全识别重发请求，
    # 无需根据相同文本猜测它属于哪一次 Run。
    origin_message_id: UUID | None = None
    scenario: str = "general"
    mode: ThreadMode = ThreadMode.NORMAL
    artifact_ids: list[UUID] = Field(default_factory=list)
    authorized_targets: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    success_conditions: list[str] = Field(default_factory=lambda: ["reference_tool_succeeded"])
    verification_rules: list[VerificationRule] = Field(default_factory=list)


class CallStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ModelCall(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    provider: str
    model: str
    duration_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    status: CallStatus
    error_category: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCall(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    tool_name: str
    input_summary: str
    result_summary: str | None = None
    duration_ms: int = Field(ge=0)
    status: CallStatus
    error: str | None = None
    artifact_ids: list[UUID] = Field(default_factory=list)


class EvidenceRecord(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    candidate: str
    source_call_id: UUID
    location: str
    verified: bool
    verification_summary: str
    rule_kind: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class RunCheckpoint(DomainModel):
    run_id: UUID
    checkpoint_sequence: int = Field(ge=1)
    node: str
    state_schema_version: str = "3.0"
    state: dict[str, Any]
    elapsed_seconds: float = Field(ge=0)
    created_at: datetime = Field(default_factory=utcnow)


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["call_tool", "replan", "finish", "fail", "request_input"]
    summary: str
    tool_name: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    candidate: EvidenceCandidate | None = None
    updated_plan: list[str] = Field(default_factory=list)
    answer: str | None = Field(default=None, max_length=100_000)
    structured_output: dict[str, Any] | None = None


class MemoryRecord(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    kind: Literal["thread_summary", "run_summary", "important_fact", "user_input"]
    content: str = Field(min_length=1, max_length=100_000)
    enabled: bool = True
    source_run_id: UUID | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ImportantFacts(BaseModel):
    """模型从一次运行中提取的少量、可复用事实。"""

    model_config = ConfigDict(extra="forbid")
    facts: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("facts")
    @classmethod
    def clean_facts(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            fact = " ".join(value.split()).strip()[:1000]
            if fact and fact.casefold() not in {item.casefold() for item in cleaned}:
                cleaned.append(fact)
        return cleaned


class VerificationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["regex", "sha256"]
    value: str = Field(min_length=1, max_length=2000)


class EvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=10000)
    source_call_id: UUID
    location: str = Field(min_length=1, max_length=500, pattern=r"^/")


class AgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=500)
    steps: list[str] = Field(min_length=1, max_length=30)
    success_approach: str = Field(min_length=1, max_length=500)
    expected_results: list[str] = Field(default_factory=list, max_length=30)
    verification_methods: list[str] = Field(default_factory=list, max_length=30)
    risks: list[str] = Field(default_factory=list, max_length=30)
    dependencies: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def complete_step_contracts(self) -> AgentPlan:
        """旧计划缺少新字段时安全补齐；显式字段则必须与步骤一一对应。"""

        if not self.expected_results:
            self.expected_results = [f"完成：{step}" for step in self.steps]
        if not self.verification_methods:
            self.verification_methods = [self.success_approach for _ in self.steps]
        if len(self.expected_results) != len(self.steps):
            raise ValueError("每个计划步骤必须有一个预期结果")
        if len(self.verification_methods) != len(self.steps):
            raise ValueError("每个计划步骤必须有一个验证方式")
        return self


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: UUID
    tool_name: str
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    summary: str
    error: str | None = None
