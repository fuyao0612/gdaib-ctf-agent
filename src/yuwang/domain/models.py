from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)
    schema_version: str = "1.0"


class ThreadMode(StrEnum):
    NORMAL = "normal"
    COMPETITION = "competition"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


ACTIVE_RUN_STATUSES = {RunStatus.QUEUED, RunStatus.RUNNING}


class MessageRole(StrEnum):
    USER = "user"
    AGENT = "agent"
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
    agent_profile_id: UUID | None = None
    agent_profile_version: int | None = Field(default=None, ge=1)
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
    attempt: int = Field(1, ge=1)
    stop_requested: bool = False
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def transition(self, target: RunStatus, error: str | None = None) -> None:
        allowed = {
            RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.STOPPED},
            RunStatus.RUNNING: {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.STOPPED},
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
    scenario: str = "safe_demo"
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
    state_schema_version: str = "2.0"
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


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: UUID
    tool_name: str
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    summary: str
    error: str | None = None
