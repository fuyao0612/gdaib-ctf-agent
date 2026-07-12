from __future__ import annotations

import asyncio
import time
from datetime import UTC
from typing import Any, TypedDict, cast
from uuid import UUID

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from yuwang.domain.models import AgentAction, EventType, RunStatus, TaskSpec, utcnow
from yuwang.events import EventService
from yuwang.model_providers import ModelProvider, ProviderError
from yuwang.policy import PolicyEngine
from yuwang.reports import ReportGenerator
from yuwang.storage import SQLiteRepository
from yuwang.tooling import ToolExecutor, ToolRegistry


class BudgetExceeded(RuntimeError):
    pass


class RunStopped(RuntimeError):
    pass


class AgentStateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    task: TaskSpec
    step: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    tokens: int = 0
    started_monotonic: float = Field(default_factory=time.monotonic)
    plan: list[str] = Field(default_factory=list)
    action: AgentAction | None = None
    last_tool_result: dict[str, Any] | None = None
    verified: bool = False


class GraphState(TypedDict, total=False):
    run_id: UUID
    task: dict[str, Any]
    step: int
    model_calls: int
    tool_calls: int
    tool_failures: int
    tokens: int
    started_monotonic: float
    plan: list[str]
    action: dict[str, Any] | None
    last_tool_result: dict[str, Any] | None
    verified: bool


class AgentEngine:
    """Framework-independent agent core. Infrastructure arrives only through interfaces."""

    def __init__(self, repository: SQLiteRepository, provider: ModelProvider, registry: ToolRegistry, policy: PolicyEngine) -> None:
        self.repository = repository
        self.provider = provider
        self.registry = registry
        self.executor = ToolExecutor(registry)
        self.policy = policy
        self.events = EventService(repository)
        self.reporter = ReportGenerator()
        self.graph = self._build_graph()

    def _state(self, raw: GraphState) -> AgentStateModel:
        return AgentStateModel.model_validate(raw)

    def _checkpoint(self, node: str, state: AgentStateModel) -> None:
        state.step += 1
        budget = state.task.budget
        run = self.repository.get_run(state.run_id)
        if run and run.stop_requested:
            raise RunStopped("用户请求停止")
        if state.step > budget.max_steps:
            raise BudgetExceeded("超过最大步骤预算")
        if state.model_calls > budget.max_model_calls:
            raise BudgetExceeded("超过模型调用预算")
        if state.tool_calls > budget.max_tool_calls:
            raise BudgetExceeded("超过工具调用预算")
        if state.tokens > budget.max_tokens:
            raise BudgetExceeded("超过 Token 预算")
        if time.monotonic() - state.started_monotonic > budget.max_duration_seconds:
            raise BudgetExceeded("超过总时长预算")
        self.repository.save_checkpoint(state.run_id, node, state.model_dump(mode="json"))

    def _result(self, node: str, state: AgentStateModel) -> GraphState:
        self._checkpoint(node, state)
        return state.model_dump(mode="python")  # type: ignore[return-value]

    async def _ingest(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        self.events.emit(state.run_id, EventType.STATUS_UPDATE, "已接收任务并建立可恢复检查点")
        return self._result("ingest", state)

    async def _normalize_task(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        state.task.body = state.task.body.strip()
        self.events.emit(state.run_id, EventType.STATUS_UPDATE, "任务已规范化", {"scenario": state.task.scenario})
        return self._result("normalize_task", state)

    async def _plan(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        state.plan = ["检查任务授权", "调用安全参考工具", "验证输出", "生成审计报告"]
        self.events.emit(state.run_id, EventType.PLAN_UPDATED, "已生成结构化执行计划", {"steps": state.plan})
        return self._result("plan", state)

    async def _policy_check(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        self.events.emit(state.run_id, EventType.POLICY_CHECKED, "任务授权存在，网络默认拒绝；允许已注册的低风险本地工具")
        return self._result("policy_check", state)

    async def _select_tool(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        prompt = f"请返回结构化 AgentAction。task={state.task.body[:300]}; tool_failures={state.tool_failures}"
        last_error: ProviderError | None = None
        for attempt in range(1, 3):
            try:
                state.action = await asyncio.wait_for(self.provider.generate_structured(prompt, AgentAction, timeout=state.task.budget.step_timeout_seconds, attempt=attempt), timeout=state.task.budget.step_timeout_seconds)
                state.model_calls += 1
                state.tokens += max(1, len(prompt) // 4) + 30
                break
            except ProviderError as exc:
                state.model_calls += 1
                last_error = exc
                self.events.emit(state.run_id, EventType.WARNING, f"模型结构化调用失败（第 {attempt} 次）：{exc.category}")
                if not exc.retryable:
                    raise
        if state.action is None:
            raise last_error or RuntimeError("模型未返回动作")
        if state.action.kind != "call_tool" or not state.action.tool_name:
            raise RuntimeError("当前执行阶段需要结构化工具动作")
        decision = self.policy.check_tool(state.task, state.action.tool_name, state.action.tool_input, self.registry.names())
        self.events.emit(state.run_id, EventType.POLICY_CHECKED, decision.reason, {"allowed": decision.allowed, "tool": state.action.tool_name})
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return self._result("select_tool", state)

    async def _execute_tool(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        assert state.action and state.action.tool_name
        state.tool_calls += 1
        self.events.emit(state.run_id, EventType.TOOL_STARTED, f"开始调用 {state.action.tool_name}", {"tool": state.action.tool_name, "input_summary": state.action.summary})
        result = await self.executor.execute(state.action.tool_name, state.action.tool_input, state.task.budget.step_timeout_seconds)
        state.last_tool_result = result.model_dump(mode="json")
        if not result.success:
            state.tool_failures += 1
        self.events.emit(state.run_id, EventType.TOOL_FINISHED, result.summary, {"tool": state.action.tool_name, "success": result.success, "duration_ms": result.duration_ms, "error": result.error.model_dump() if result.error else None})
        return self._result("execute_tool", state)

    async def _observe(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        success = bool(state.last_tool_result and state.last_tool_result.get("success"))
        self.events.emit(state.run_id, EventType.STATUS_UPDATE, "工具结果已标准化并记录" if success else "工具失败已隔离，准备重新规划")
        return self._result("observe", state)

    async def _verify(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        state.verified = bool(state.last_tool_result and state.last_tool_result.get("success"))
        self.events.emit(state.run_id, EventType.STATUS_UPDATE, "成功条件已验证" if state.verified else "成功条件尚未满足")
        return self._result("verify", state)

    async def _replan(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        self.events.emit(state.run_id, EventType.REPLANNED, "首次工具调用失败；保留证据并采用无故障参数重试")
        return self._result("replan", state)

    async def _complete(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        self.events.emit(state.run_id, EventType.STATUS_UPDATE, "执行闭环完成，正在生成报告")
        return self._result("complete", state)

    async def _generate_report(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        run = self.repository.get_run(state.run_id)
        if not run:
            raise RuntimeError("run disappeared")
        run.transition(RunStatus.COMPLETED)
        self.repository.save_run(run)
        duration_ms = int((time.monotonic() - state.started_monotonic) * 1000)
        events = self.repository.list_events(run.id)
        markdown, data = self.reporter.generate(run, state.task, events, {"model_calls": state.model_calls, "tool_calls": state.tool_calls, "tool_failures": state.tool_failures, "tokens": state.tokens, "duration_ms": duration_ms})
        self.repository.save_report(run.id, markdown, data)
        self.events.emit(run.id, EventType.RUN_COMPLETED, "运行完成，最终报告已生成", {"report_available": True})
        return self._result("generate_report", state)

    def _route_verify(self, raw: GraphState) -> str:
        return "complete" if self._state(raw).verified else "replan"

    def _build_graph(self) -> Any:
        graph = StateGraph(GraphState)
        # LangGraph's overloads currently reject otherwise valid async TypedDict
        # nodes in mypy. Keep the localized adapter dynamic; every node itself is
        # still strictly typed and validates through AgentStateModel.
        add_node = cast(Any, graph.add_node)
        add_node("ingest", self._ingest)
        add_node("normalize_task", self._normalize_task)
        add_node("plan", self._plan)
        add_node("policy_check", self._policy_check)
        add_node("select_tool", self._select_tool)
        add_node("execute_tool", self._execute_tool)
        add_node("observe", self._observe)
        add_node("verify", self._verify)
        add_node("replan", self._replan)
        add_node("complete", self._complete)
        add_node("generate_report", self._generate_report)
        graph.set_entry_point("ingest")
        graph.add_edge("ingest", "normalize_task")
        graph.add_edge("normalize_task", "plan")
        graph.add_edge("plan", "policy_check")
        graph.add_edge("policy_check", "select_tool")
        graph.add_edge("select_tool", "execute_tool")
        graph.add_edge("execute_tool", "observe")
        graph.add_edge("observe", "verify")
        graph.add_conditional_edges("verify", self._route_verify, {"complete": "complete", "replan": "replan"})
        graph.add_edge("replan", "select_tool")
        graph.add_edge("complete", "generate_report")
        graph.add_edge("generate_report", END)
        return graph.compile()

    async def run(self, run_id: UUID, task: TaskSpec) -> None:
        run = self.repository.get_run(run_id)
        if not run:
            raise KeyError("run not found")
        run.transition(RunStatus.RUNNING)
        self.repository.save_run(run)
        self.events.emit(run.id, EventType.RUN_STARTED, "Agent 运行已开始", {"provider": run.provider})
        initial = AgentStateModel(run_id=run.id, task=task)
        try:
            await self.graph.ainvoke(initial.model_dump(mode="python"))
        except RunStopped as exc:
            run = self.repository.get_run(run_id) or run
            run.transition(RunStatus.STOPPED, str(exc))
            run.finished_at = run.finished_at.astimezone(UTC) if run.finished_at else utcnow()
            self.repository.save_run(run)
            self.events.emit(run.id, EventType.RUN_STOPPED, "运行已按请求安全停止")
        except Exception as exc:
            run = self.repository.get_run(run_id) or run
            run.transition(RunStatus.FAILED, str(exc)[:500])
            self.repository.save_run(run)
            self.events.emit(run.id, EventType.RUN_FAILED, "运行安全终止", {"error": run.error})
            events = self.repository.list_events(run.id)
            markdown, data = self.reporter.generate(run, task, events, {})
            self.repository.save_report(run.id, markdown, data)
