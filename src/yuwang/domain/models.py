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
    """Deprecated SQLite compatibility marker; new flows always create Agent Runs."""

    CHAT = "chat"
    AGENT = "agent"


ToolSelectionMode = Literal["all", "selected"]
ThreadToolSelectionMode = Literal["inherit", "selected"]
SecurityScenario = Literal[
    "general",
    "ctf",
    "incident_response",
    "vulnerability_analysis",
    "reverse_static",
]


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
ArtifactTrustLevel = Literal["untrusted", "user_asserted", "tool_verified"]
ResultType = Literal[
    "answer",
    "finding",
    "assessment",
    "flag",
    "artifact",
    "handoff",
    "indicator",
    "vulnerability",
    "patch",
]


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
    TOOL_PROGRESS = "tool_progress"
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
    CONTEXT_COMPACTED = "context_compacted"
    TASK_BRIEF_CREATED = "task_brief_created"
    CLARIFICATION_REQUESTED = "clarification_requested"
    CLARIFICATION_RECEIVED = "clarification_received"
    PLAN_CREATED = "plan_created"
    PLAN_APPROVAL_REQUESTED = "plan_approval_requested"
    PLAN_EDITED = "plan_edited"
    PLAN_APPROVED = "plan_approved"
    PLAN_REJECTED = "plan_rejected"
    RISK_APPROVAL_REQUESTED = "risk_approval_requested"
    RISK_APPROVED = "risk_approved"
    RISK_REJECTED = "risk_rejected"
    GUIDANCE_QUEUED = "guidance_queued"
    GUIDANCE_APPLIED = "guidance_applied"
    GUIDANCE_SKIPPED = "guidance_skipped"
    PAUSE_REQUESTED = "pause_requested"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_steps: int = Field(100, ge=1, le=100)
    max_model_calls: int = Field(50, ge=1, le=50)
    max_tool_calls: int = Field(50, ge=1, le=50)
    max_tokens: int = Field(1_000_000, ge=1, le=1_000_000)
    max_model_cost: float = Field(100.0, ge=0, le=100_000)
    max_duration_seconds: float = Field(1_800, gt=0, le=3600)
    step_timeout_seconds: float = Field(300, gt=0, le=300)


class Thread(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=160)
    mode: ThreadMode = ThreadMode.NORMAL
    scenario: SecurityScenario = "general"
    # Deprecated：仅用于读取历史 SQLite JSON，UI 与新建流程均不再使用。
    interaction_mode: InteractionMode = InteractionMode.AGENT
    # 对话级模型选择独立于全局默认值。Run 启动时再把实际 Provider 固化为快照，
    # 因此用户切换这里的值绝不会改变已经运行中的任务。
    provider_config_id: UUID | None = None
    # 已失效的会话选择被安全回退时保留一次性提示，前端确认展示后会清空它。
    provider_fallback_notice: str | None = None
    # 对话只保存当前选择；真正运行时会把 Skill 内容复制进不可变 TaskSpec 快照。
    skill_ids: list[UUID] = Field(default_factory=list, max_length=20)
    # Profile 可以给出默认白名单；Thread 只允许继承或进一步缩小，绝不自行扩权。
    tool_selection_mode: ThreadToolSelectionMode = "inherit"
    tool_ids: list[str] = Field(default_factory=list, max_length=100)
    agent_profile_id: UUID | None = None
    agent_profile_version: int | None = Field(default=None, ge=1)
    plan_mode: Literal["auto", "approval"] = "auto"
    archived: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def normalize_tool_selection(self) -> Thread:
        if len(self.tool_ids) != len(set(self.tool_ids)):
            raise ValueError("工具 ID 不能重复")
        if self.tool_selection_mode == "inherit":
            self.tool_ids = []
        return self


class Message(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    role: MessageRole
    content: str = Field(min_length=1, max_length=100_000)
    artifact_ids: list[UUID] = Field(default_factory=list)
    # 新 Agent 消息绑定不可变 Run 与真实模型记录；旧 SQLite JSON 没有这些字段时保持为空。
    run_id: UUID | None = None
    provider: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    model_is_fallback: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Run(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    status: RunStatus = RunStatus.QUEUED
    provider: str = "unconfigured"
    model: str | None = Field(default=None, max_length=160)
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
    results: list[TaskResult] = Field(default_factory=list, max_length=100)
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
    source: str = Field(default="user_upload", min_length=1, max_length=200)
    trust_level: ArtifactTrustLevel = "untrusted"
    extracted_metadata: dict[str, Any] = Field(default_factory=dict)
    preview: str | None = Field(default=None, max_length=12_000)
    contains_prompt_injection: bool = False
    truncated: bool = False
    original_ref: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=utcnow)

    @field_validator("storage_ref")
    @classmethod
    def reject_absolute_storage_ref(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if (
            value.startswith(("/", "\\"))
            or ":\\" in value
            or ":/" in value
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise ValueError("storage_ref must be an opaque relative reference")
        return value


class EvidenceReference(DomainModel):
    """通用结果引用的最小证据契约。"""

    id: UUID = Field(default_factory=uuid4)
    evidence_type: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=500)
    content_summary: str = Field(min_length=1, max_length=2000)
    raw_ref: str = Field(min_length=1, max_length=500)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    collected_at: datetime = Field(default_factory=utcnow)
    source_step: int | None = Field(default=None, ge=1)
    reliable: bool = False
    tool_verified: bool = False


class TaskResult(DomainModel):
    """跨场景统一结果；CTF Flag 只是 ``result_type=flag`` 的一种场景结果。"""

    id: UUID = Field(default_factory=uuid4)
    result_type: ResultType
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=10_000)
    structured_data: dict[str, Any] = Field(default_factory=dict)
    scenario: str = Field(min_length=1, max_length=80)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=100)
    validation_status: ValidationStatus = "pending"
    validator_name: str = Field(default="none", min_length=1, max_length=120)
    validator_version: str = Field(default="0", min_length=1, max_length=40)
    validated_at: datetime | None = None
    validation_explanation: str = Field(default="", max_length=2000)
    confidence: float = Field(default=0, ge=0, le=1)
    source_steps: list[int] = Field(default_factory=list, max_length=100)
    tool_call_ids: list[UUID] = Field(default_factory=list, max_length=100)
    created_at: datetime = Field(default_factory=utcnow)


Run.model_rebuild()


class SkillSnapshot(BaseModel):
    """一次 Run 使用的声明式 Skill 快照，不包含代码或可执行载荷。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    skill_id: UUID
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    prompt: str = Field(min_length=1, max_length=10_000)
    steps: list[str] = Field(default_factory=list, max_length=30)
    checklist: list[str] = Field(default_factory=list, max_length=30)


class ToolSnapshot(BaseModel):
    """Run 开始时固化的工具协议快照，不随注册表或设置中心变化。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str = Field(min_length=1, max_length=240)
    namespace: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=80)
    source_type: Literal["builtin", "python_plugin", "mcp"]
    source: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=2_000)
    capabilities: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
    risk: Literal["low", "medium", "high"]
    permissions: list[str] = Field(default_factory=list)
    requires_network: bool
    allowed_target_types: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(gt=0, le=120)
    error_codes: list[str] = Field(default_factory=list)
    idempotent: bool
    artifact_types: list[str] = Field(default_factory=list)
    consumes: list[str] = Field(default_factory=list, max_length=30)
    produces: list[str] = Field(default_factory=list, max_length=30)
    prerequisites: list[str] = Field(default_factory=list, max_length=30)
    enables: list[str] = Field(default_factory=list, max_length=50)
    fallback_capabilities: list[str] = Field(default_factory=list, max_length=30)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    config_schema: dict[str, Any] = Field(default_factory=dict)
    supports_cancellation: bool = False
    supports_progress: bool = False


class KnowledgeMatchSnapshot(DomainModel):
    """模型可见的 RAG 片段快照；它始终属于不可信参考资料。"""

    document_id: UUID
    chunk_id: UUID
    title: str = Field(min_length=1, max_length=200)
    source_uri: str | None = Field(default=None, max_length=2_048)
    chunk_ordinal: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=2_400)
    content_sha256: str = Field(min_length=64, max_length=64)
    score: float = Field(ge=0)


class TaskSpec(DomainModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True, frozen=True)
    body: str = Field(min_length=1, max_length=100_000)
    # TaskSpec 是不可变运行快照；保留来源消息可让统一入口安全识别重发请求，
    # 无需根据相同文本猜测它属于哪一次 Run。
    origin_message_id: UUID | None = None
    scenario: str = "general"
    mode: ThreadMode = ThreadMode.NORMAL
    artifact_ids: list[UUID] = Field(default_factory=list)
    # RAG 命中在 Run 开始时固化；知识文档后续更新不会改写历史上下文。
    knowledge_matches: list[KnowledgeMatchSnapshot] = Field(default_factory=list, max_length=8)
    authorized_targets: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    success_conditions: list[str] = Field(default_factory=lambda: ["reference_tool_succeeded"])
    verification_rules: list[VerificationRule] = Field(default_factory=list)
    # 运行开始后 Skill 不再跟随设置中心修改，恢复与审计均使用这里的快照。
    skills: list[SkillSnapshot] = Field(default_factory=list, max_length=20)
    # 与 Provider/Profile 一样，工具定义也在运行开始时冻结。旧 Run 缺少该字段时
    # 保持可恢复，并仅在明确兼容路径中读取当前显式注册工具。
    tool_snapshots: list[ToolSnapshot] = Field(default_factory=list, max_length=100)
    # 序列化检查点会补齐默认字段，不能用字段是否存在区分历史 Run 与空白名单。
    tool_snapshot_frozen: bool = False

    @model_validator(mode="before")
    @classmethod
    def mark_explicit_tool_snapshot(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "tool_snapshots" in data and "tool_snapshot_frozen" not in data:
            data["tool_snapshot_frozen"] = True
        return data

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
    tool_id: str | None = None
    tool_version: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    target_scope: list[str] = Field(default_factory=list)
    approval_fingerprint: str | None = None
    input_summary: str
    result_summary: str | None = None
    duration_ms: int = Field(ge=0)
    status: CallStatus
    error: str | None = None
    artifact_ids: list[UUID] = Field(default_factory=list)


class ExecutionStep(DomainModel):
    """面向用户的公开执行步骤，不保存模型隐藏推理或完整工具输出。"""

    run_id: UUID
    sequence: int = Field(ge=1)
    call_id: UUID | None = None
    goal: str = Field(min_length=1, max_length=500)
    action_kind: str = Field(min_length=1, max_length=80)
    action_summary: str = Field(min_length=1, max_length=500)
    # 兼容历史运行；新步骤创建时保存已脱敏的公开行动理由。
    action_reason: str | None = Field(default=None, max_length=600)
    tool_id: str | None = Field(default=None, max_length=240)
    tool_name: str | None = Field(default=None, max_length=160)
    arguments: dict[str, Any] = Field(default_factory=dict)
    observation_status: Literal["running", "success", "error", "timeout", "blocked", "stopped"] = (
        "running"
    )
    observation_summary: str | None = Field(default=None, max_length=1000)
    observation_facts: list[str] = Field(default_factory=list, max_length=20)
    observation_details: dict[str, Any] = Field(default_factory=dict)
    reproduction_hint: str | None = Field(default=None, max_length=1000)
    preview: str | None = Field(default=None, max_length=4000)
    error: str | None = Field(default=None, max_length=1000)
    decision: str | None = Field(default=None, max_length=500)
    artifact_ids: list[UUID] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class EvidenceRecord(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    candidate: str
    source_call_id: UUID
    location: str
    verified: bool
    verification_summary: str
    rule_kind: str | None = None
    source_step: int | None = Field(default=None, ge=1)
    discovery_source: str = "evidence"
    format_status: Literal["not_checked", "format_matched", "format_failed"] = "not_checked"
    verification_scope: Literal["none", "format", "deterministic_rule", "platform"] = "none"
    deterministic_validation_status: Literal["not_run", "passed", "failed"] = "not_run"
    platform_validation_status: Literal["not_run", "passed", "failed"] = "not_run"
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
    # 历史检查点可能没有理由；模型草稿仍要求 reason。
    action_reason: str | None = Field(default=None, max_length=600)
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
    # 摘要游标、版本与公开引用等持久化元数据。历史 JSON 缺少字段时安全为空。
    metadata: dict[str, Any] = Field(default_factory=dict)
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
    step_details: list[PlanStep] = Field(default_factory=list, max_length=30)

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
        if not self.step_details:
            self.step_details = [
                PlanStep(
                    step_id=f"step-{index}",
                    goal=step,
                    reason="按任务目标推进并收集公开证据。",
                    expected_result=self.expected_results[index - 1],
                    verification_method=self.verification_methods[index - 1],
                )
                for index, step in enumerate(self.steps, 1)
            ]
        if (
            len(self.step_details) != len(self.steps)
            or [item.goal for item in self.step_details] != self.steps
        ):
            # 兼容旧 UI 只提交并行数组的计划编辑；服务端重新对齐结构化字段。
            self.step_details = [
                PlanStep(
                    step_id=f"step-{index}",
                    goal=step,
                    reason=(
                        self.step_details[index - 1].reason
                        if index <= len(self.step_details)
                        else "按更新后的任务目标推进并收集公开证据。"
                    ),
                    expected_result=self.expected_results[index - 1],
                    verification_method=self.verification_methods[index - 1],
                    capabilities=(
                        self.step_details[index - 1].capabilities
                        if index <= len(self.step_details)
                        else []
                    ),
                    dependencies=(
                        self.step_details[index - 1].dependencies
                        if index <= len(self.step_details)
                        else []
                    ),
                    risk=(
                        self.step_details[index - 1].risk
                        if index <= len(self.step_details)
                        else "low"
                    ),
                    status=(
                        self.step_details[index - 1].status
                        if index <= len(self.step_details)
                        else "planned"
                    ),
                )
                for index, step in enumerate(self.steps, 1)
            ]
        return self


class PlanStep(BaseModel):
    """可审计的公开计划步骤；不包含模型隐藏推理。"""

    model_config = ConfigDict(extra="forbid")
    step_id: str = Field(pattern=r"^step-[1-9][0-9]{0,2}$")
    goal: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=600)
    expected_result: str = Field(min_length=1, max_length=1000)
    verification_method: str = Field(min_length=1, max_length=1000)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    risk: Literal["low", "medium", "high"] = "low"
    status: Literal["planned", "running", "succeeded", "failed", "skipped", "replanned"] = "planned"


AgentPlan.model_rebuild()


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: UUID
    tool_name: str
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    summary: str
    error: str | None = None
