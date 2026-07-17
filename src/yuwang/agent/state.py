"""Agent 运行状态、LangGraph 载荷和可预期控制异常。

``AgentStateModel`` 是每个节点之间唯一可信的状态结构；检查点写入前都会经过
Pydantic 校验。``GraphState`` 仅用于 LangGraph 的类型提示，不能绕过领域校验。
"""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from yuwang.control import TaskBrief
from yuwang.domain.models import AgentAction, AgentPlan, Observation, TaskSpec


class BudgetExceeded(RuntimeError):
    """运行消耗超过 TaskSpec 固化的预算。"""


class RunStopped(RuntimeError):
    """用户请求停止，运行循环应安全退出。"""


class RunPaused(RuntimeError):
    """暂停请求已在安全检查点生效，运行可从下一节点继续。"""


class AgentDeclaredFailure(RuntimeError):
    """Agent 检测到循环、漂移或不安全状态并主动失败。"""


class AgentStateModel(BaseModel):
    """可持久化、可恢复的 Agent 完整状态。"""

    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    task: TaskSpec
    step: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    tokens: int = 0
    model_cost: float = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0, ge=0)
    task_brief: TaskBrief | None = None
    plan_approved: bool = False
    plan: AgentPlan | None = None
    action: AgentAction | None = None
    observations: list[Observation] = Field(default_factory=list)
    action_fingerprints: list[str] = Field(default_factory=list)
    plan_fingerprints: list[str] = Field(default_factory=list)
    context_anchor: str | None = None
    no_progress_count: int = 0
    replan_count: int = 0
    verified: bool = False
    verification_summary: str = "尚未验证"
    validation_status: str = "pending"
    evidence_level: str = "none"
    supplemental_inputs: list[str] = Field(default_factory=list)
    guidance_replan_required: bool = False
    context_tokens: int = 0
    observation_chars: int = 0
    context_truncations: int = 0
    final_answer: str | None = None
    structured_output: dict[str, Any] | None = None
    tool_schemas: list[dict[str, Any]] = Field(default_factory=list)
    remaining_budget: dict[str, float | int] = Field(default_factory=dict)


class GraphState(TypedDict, total=False):
    """LangGraph 节点传递的序列化字段，与 AgentStateModel 一一对应。"""

    run_id: UUID
    task: dict[str, Any]
    step: int
    model_calls: int
    tool_calls: int
    tool_failures: int
    tokens: int
    model_cost: float
    elapsed_seconds: float
    task_brief: dict[str, Any] | None
    plan_approved: bool
    plan: dict[str, Any] | None
    action: dict[str, Any] | None
    observations: list[dict[str, Any]]
    action_fingerprints: list[str]
    plan_fingerprints: list[str]
    context_anchor: str | None
    no_progress_count: int
    replan_count: int
    verified: bool
    verification_summary: str
    validation_status: str
    evidence_level: str
    supplemental_inputs: list[str]
    guidance_replan_required: bool
    context_tokens: int
    observation_chars: int
    context_truncations: int
    final_answer: str | None
    structured_output: dict[str, Any] | None
    tool_schemas: list[dict[str, Any]]
    remaining_budget: dict[str, float | int]
