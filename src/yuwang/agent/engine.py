from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any, TypedDict, cast
from uuid import UUID, uuid4

from jsonschema import ValidationError as JsonSchemaValidationError  # type: ignore[import-untyped]
from jsonschema import validate as validate_json_schema
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from yuwang.agent.components import (
    AgentComponents,
    default_components,
)
from yuwang.agent.repository import AgentRepository
from yuwang.domain.models import (
    AgentAction,
    AgentPlan,
    CallStatus,
    EventType,
    EvidenceRecord,
    MemoryRecord,
    ModelCall,
    Observation,
    RunStatus,
    TaskSpec,
    ToolCall,
)
from yuwang.events import EventService
from yuwang.model_providers import ModelProvider, ProviderError
from yuwang.policy import PolicyEngine
from yuwang.settings import AgentProfileInput, AgentProfileVersion, SafeTemplateRenderer
from yuwang.tooling import ToolExecutor, ToolRegistry


class BudgetExceeded(RuntimeError):
    pass


class RunStopped(RuntimeError):
    pass


class AgentDeclaredFailure(RuntimeError):
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
    model_cost: float = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0, ge=0)
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
    context_tokens: int = 0
    observation_chars: int = 0
    context_truncations: int = 0
    final_answer: str | None = None
    structured_output: dict[str, Any] | None = None
    tool_schemas: list[dict[str, Any]] = Field(default_factory=list)
    remaining_budget: dict[str, float | int] = Field(default_factory=dict)


class GraphState(TypedDict, total=False):
    run_id: UUID
    task: dict[str, Any]
    step: int
    model_calls: int
    tool_calls: int
    tool_failures: int
    tokens: int
    model_cost: float
    elapsed_seconds: float
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
    context_tokens: int
    observation_chars: int
    context_truncations: int
    final_answer: str | None
    structured_output: dict[str, Any] | None
    tool_schemas: list[dict[str, Any]]
    remaining_budget: dict[str, float | int]


class AgentEngine:
    """Provider/tool agnostic autonomous loop with deterministic completion authority."""

    def __init__(
        self,
        repository: AgentRepository,
        provider: ModelProvider,
        registry: ToolRegistry,
        policy: PolicyEngine,
        *,
        profile: AgentProfileVersion | None = None,
        artifact_root: Path | None = None,
        components: AgentComponents | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.registry = registry
        self.executor = ToolExecutor(registry)
        self.policy = policy
        self.events = EventService(repository)
        self.profile = profile or AgentProfileVersion(
            **AgentProfileInput(name="默认安全 Agent").model_dump(), version=1
        )
        self.components = components or default_components(
            repository, artifact_root or Path("data/artifacts")
        )
        self.context_builder = self.components.context_builder
        self.planner = self.components.planner
        self.action_selector = self.components.action_selector
        self.reporter = self.components.report_renderer
        self.verifier = self.components.verifier
        self._last_tick: dict[UUID, float] = {}
        self.graph = self._build_graph()

    def _state(self, raw: GraphState) -> AgentStateModel:
        return AgentStateModel.model_validate(raw)

    def _checkpoint(self, node: str, state: AgentStateModel) -> None:
        now = time.monotonic()
        previous_tick = self._last_tick.get(state.run_id, now)
        state.elapsed_seconds += max(0.0, now - previous_tick)
        self._last_tick[state.run_id] = now
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
        if state.model_cost > budget.max_model_cost:
            raise BudgetExceeded("超过模型费用预算")
        if state.elapsed_seconds > budget.max_duration_seconds:
            raise BudgetExceeded("超过总时长预算")
        self.repository.save_checkpoint(state.run_id, node, state.model_dump(mode="json"))

    def _result(self, node: str, state: AgentStateModel) -> GraphState:
        self._checkpoint(node, state)
        return cast(GraphState, state.model_dump(mode="python"))

    def _context(self, state: AgentStateModel, purpose: str) -> str:
        budget = state.task.budget
        anchor = hashlib.sha256(
            (
                state.task.model_dump_json()
                + str(self.profile.profile_id)
                + str(self.profile.version)
            ).encode()
        ).hexdigest()
        if state.context_anchor and state.context_anchor != anchor:
            raise AgentDeclaredFailure("检测到任务或配置上下文漂移")
        state.context_anchor = anchor
        state.tool_schemas = [spec.model_dump(mode="json") for spec in self.registry.specs()]
        state.remaining_budget = {
            "steps": budget.max_steps - state.step,
            "model_calls": budget.max_model_calls - state.model_calls,
            "tool_calls": budget.max_tool_calls - state.tool_calls,
            "tokens": budget.max_tokens - state.tokens,
            "model_cost": budget.max_model_cost - state.model_cost,
        }
        result = self.context_builder.build(state, self.profile, purpose)
        state.context_tokens = result.estimated_tokens
        state.observation_chars = result.observation_chars
        if result.truncated:
            state.context_truncations += 1
            self.events.emit(
                state.run_id,
                EventType.CONTEXT_TRUNCATED,
                "上下文已按配置预算进行可审计裁剪",
                {"reasons": result.reasons, "estimated_tokens": result.estimated_tokens},
            )
        return result.prompt

    async def _model_call(
        self, state: AgentStateModel, output_type: type[BaseModel], purpose: str
    ) -> BaseModel:
        prompt = self._context(state, purpose)
        estimated_input_tokens = max(1, len(prompt) // 4)
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self.provider.generate_structured(
                    prompt,
                    output_type,
                    timeout=state.task.budget.step_timeout_seconds,
                ),
                timeout=state.task.budget.step_timeout_seconds,
            )
        except Exception as exc:
            category = (
                exc.category
                if isinstance(exc, ProviderError)
                else "timeout"
                if isinstance(exc, TimeoutError)
                else "service"
            )
            metrics = exc.metrics if isinstance(exc, ProviderError) else None
            request_count = metrics.request_count if metrics else 1
            input_tokens = (
                metrics.input_tokens
                if metrics and metrics.usage_reported
                else estimated_input_tokens
            )
            state.model_calls += request_count
            state.tokens += input_tokens
            state.model_cost += metrics.cost if metrics else 0
            self.repository.save_model_call(
                ModelCall(
                    run_id=state.run_id,
                    provider=metrics.provider if metrics else self.provider.name,
                    model=metrics.model
                    if metrics
                    else str(getattr(self.provider, "model", "provider-chain")),
                    duration_ms=metrics.duration_ms
                    if metrics
                    else int((time.perf_counter() - started) * 1000),
                    input_tokens=input_tokens,
                    output_tokens=0,
                    status=CallStatus.FAILED,
                    error_category=str(category),
                    metadata={
                        "purpose": purpose,
                        "request_count": request_count,
                        "retry_count": metrics.retry_count if metrics else 0,
                        "usage_reported": metrics.usage_reported if metrics else False,
                        "cost": metrics.cost if metrics else 0,
                    },
                )
            )
            raise
        metrics = getattr(self.provider, "last_call_metrics", None)
        request_count = metrics.request_count if metrics else 1
        input_tokens = (
            metrics.input_tokens if metrics and metrics.usage_reported else estimated_input_tokens
        )
        output_tokens = (
            metrics.output_tokens
            if metrics and metrics.usage_reported
            else max(1, len(result.model_dump_json()) // 4)
        )
        state.model_calls += request_count
        state.tokens += input_tokens + output_tokens
        state.model_cost += metrics.cost if metrics else 0
        self.repository.save_model_call(
            ModelCall(
                run_id=state.run_id,
                provider=metrics.provider if metrics else self.provider.name,
                model=metrics.model
                if metrics
                else str(getattr(self.provider, "model", "provider-chain")),
                duration_ms=metrics.duration_ms
                if metrics
                else int((time.perf_counter() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                status=CallStatus.SUCCEEDED,
                metadata={
                    "purpose": purpose,
                    "request_count": request_count,
                    "retry_count": metrics.retry_count if metrics else 0,
                    "usage_reported": metrics.usage_reported if metrics else False,
                    "total_tokens": metrics.total_tokens
                    if metrics
                    else input_tokens + output_tokens,
                    "cost": metrics.cost if metrics else 0,
                },
            )
        )
        return result

    async def _ingest(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        self.events.emit(state.run_id, EventType.STATUS_UPDATE, "已载入不可变任务快照")
        return self._result("ingest", state)

    async def _normalize_task(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        state.task = state.task.model_copy(update={"body": state.task.body.strip()})
        self.events.emit(
            state.run_id,
            EventType.STATUS_UPDATE,
            "任务已规范化",
            {"scenario": state.task.scenario},
        )
        return self._result("normalize_task", state)

    async def _plan(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        plan = await self.planner.plan(state, cast(Any, self._model_call))
        state.plan = AgentPlan.model_validate(plan)
        self._track_plan_progress(state)
        self.events.emit(
            state.run_id,
            EventType.PLAN_UPDATED,
            state.plan.summary,
            {"steps": state.plan.steps, "success_approach": state.plan.success_approach},
        )
        return self._result("plan", state)

    async def _select_action(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        action = await self.action_selector.select(state, cast(Any, self._model_call))
        state.action = AgentAction.model_validate(action)
        fingerprint = self._fingerprint(state.action)
        repeats = state.action_fingerprints.count(fingerprint)
        state.action_fingerprints.append(fingerprint)
        if repeats >= 2:
            state.no_progress_count += 1
            state.action = AgentAction(
                kind="replan",
                summary="检测到重复动作，强制重新规划",
            )
            self.events.emit(state.run_id, EventType.WARNING, "检测到重复动作，已阻止再次执行")
        if state.no_progress_count >= 3:
            raise AgentDeclaredFailure("连续无进展，已安全终止")
        self.events.emit(
            state.run_id,
            EventType.STATUS_UPDATE,
            state.action.summary,
            {"action": state.action.kind},
        )
        return self._result("select_action", state)

    async def _policy_check(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        if not state.action or state.action.kind != "call_tool" or not state.action.tool_name:
            raise AgentDeclaredFailure("工具动作缺少必要字段")
        tool = self.registry.get(state.action.tool_name)
        decision = self.policy.check_tool(state.task, tool.spec, state.action.tool_input)
        self.events.emit(
            state.run_id,
            EventType.POLICY_CHECKED,
            decision.reason,
            {"allowed": decision.allowed, "tool": state.action.tool_name},
        )
        if not decision.allowed:
            state.observations.append(
                Observation(
                    call_id=uuid4(),
                    tool_name=state.action.tool_name,
                    success=False,
                    summary="策略拒绝工具动作",
                    error=decision.reason,
                )
            )
            state.action = AgentAction(kind="replan", summary="策略拒绝后重新规划")
        return self._result("policy_check", state)

    async def _execute_tool(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        if not state.action or not state.action.tool_name:
            raise AgentDeclaredFailure("没有可执行工具动作")
        state.tool_calls += 1
        call_id = uuid4()
        self.repository.save_tool_call(
            ToolCall(
                id=call_id,
                run_id=state.run_id,
                tool_name=state.action.tool_name,
                input_summary=state.action.summary,
                duration_ms=0,
                status=CallStatus.STARTED,
            )
        )
        self.events.emit(
            state.run_id,
            EventType.TOOL_STARTED,
            f"开始调用 {state.action.tool_name}",
            {"call_id": str(call_id), "tool": state.action.tool_name},
        )
        result = await self.executor.execute(
            state.action.tool_name,
            state.action.tool_input,
            state.task.budget.step_timeout_seconds,
        )
        if not result.success:
            state.tool_failures += 1
        self.repository.save_tool_call(
            ToolCall(
                id=call_id,
                run_id=state.run_id,
                tool_name=state.action.tool_name,
                input_summary=state.action.summary,
                result_summary=result.summary,
                duration_ms=result.duration_ms,
                status=CallStatus.SUCCEEDED if result.success else CallStatus.FAILED,
                error=result.error.message if result.error else None,
                artifact_ids=[UUID(value) for value in result.artifact_ids],
            )
        )
        observation = Observation(
            call_id=call_id,
            tool_name=state.action.tool_name,
            success=result.success,
            output=result.output,
            summary=result.summary,
            error=result.error.message if result.error else None,
        )
        if state.observations and self._observation_digest(
            state.observations[-1]
        ) == self._observation_digest(observation):
            state.no_progress_count += 1
        else:
            state.no_progress_count = 0
        state.observations.append(observation)
        self.events.emit(
            state.run_id,
            EventType.TOOL_FINISHED,
            result.summary,
            {
                "call_id": str(call_id),
                "tool": state.action.tool_name,
                "success": result.success,
                "duration_ms": result.duration_ms,
                "error": result.error.model_dump() if result.error else None,
            },
        )
        return self._result("execute_tool", state)

    async def _observe(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        latest = state.observations[-1]
        self.events.emit(
            state.run_id,
            EventType.STATUS_UPDATE,
            "工具结果已作为不可信观察记录",
            {"call_id": str(latest.call_id), "success": latest.success},
        )
        return self._result("observe", state)

    async def _replan(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        state.replan_count += 1
        plan = await self._model_call(state, AgentPlan, "根据历史观察重新规划")
        state.plan = AgentPlan.model_validate(plan)
        self._track_plan_progress(state)
        self.events.emit(
            state.run_id,
            EventType.REPLANNED,
            state.plan.summary,
            {"steps": state.plan.steps, "replan_count": state.replan_count},
        )
        return self._result("replan", state)

    async def _verify(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        if self.profile.completion_mode == "advisory":
            if not state.action or not state.action.answer:
                raise AgentDeclaredFailure("建议回答模式缺少模型答案")
            state.verified = True
            state.validation_status = "unverified"
            state.evidence_level = "model"
            state.final_answer = state.action.answer
            state.verification_summary = "模型生成，未经外部验证"
            self.events.emit(
                state.run_id,
                EventType.STATUS_UPDATE,
                state.verification_summary,
                {"verified": False, "evidence_level": "model"},
            )
            return self._result("verify", state)
        if self.profile.completion_mode == "structured":
            if not state.action or state.action.structured_output is None:
                raise AgentDeclaredFailure("结构化输出模式缺少输出对象")
            schema = self.profile.validation_policy.json_schema
            if not schema:
                raise AgentDeclaredFailure("结构化输出模式未配置 JSON Schema")
            try:
                validate_json_schema(instance=state.action.structured_output, schema=schema)
            except JsonSchemaValidationError as exc:
                state.verification_summary = f"结构化输出校验失败：{exc.message[:200]}"
                return self._result("verify", state)
            state.verified = True
            state.validation_status = "validated"
            state.evidence_level = "structured"
            state.structured_output = state.action.structured_output
            state.verification_summary = "结构化输出已通过配置的 JSON Schema 校验"
            return self._result("verify", state)
        candidate = state.action.candidate if state.action else None
        result = self.verifier.verify(state.task, candidate, state.observations)
        state.verified = result.verified
        state.validation_status = "validated" if result.verified else "pending"
        state.evidence_level = "external" if result.verified else "none"
        state.verification_summary = result.summary
        if candidate:
            self.repository.save_evidence(
                EvidenceRecord(
                    run_id=state.run_id,
                    candidate=candidate.value,
                    source_call_id=candidate.source_call_id,
                    location=candidate.location,
                    verified=result.verified,
                    verification_summary=result.summary,
                    rule_kind=result.rule_kind,
                )
            )
        self.events.emit(
            state.run_id,
            EventType.STATUS_UPDATE,
            result.summary,
            {
                "verified": result.verified,
                "evidence_call_id": result.evidence_call_id,
                "rule_kind": result.rule_kind,
            },
        )
        return self._result("verify", state)

    async def _complete(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        if not state.verified:
            raise AgentDeclaredFailure("未通过确定性成功验证")
        self.events.emit(state.run_id, EventType.STATUS_UPDATE, "验证通过，正在生成报告")
        return self._result("complete", state)

    async def _request_input(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        run = self.repository.get_run(state.run_id)
        if not run:
            raise AgentDeclaredFailure("运行记录不存在")
        run.transition(RunStatus.WAITING_INPUT)
        self.repository.save_run(run)
        self.events.emit(
            state.run_id,
            EventType.RUN_WAITING_INPUT,
            state.action.summary if state.action else "等待用户补充信息",
            {"request_count": len(state.supplemental_inputs) + 1},
        )
        return self._result("request_input", state)

    async def _generate_report(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        run = self.repository.get_run(state.run_id)
        if not run:
            raise RuntimeError("运行记录不存在")
        run.completion_mode = self.profile.completion_mode
        run.validation_status = cast(Any, state.validation_status)
        run.evidence_level = cast(Any, state.evidence_level)
        run.transition(RunStatus.COMPLETED)
        self.repository.save_run(run)
        duration_ms = int(state.elapsed_seconds * 1000)
        events = self.repository.list_events(run.id)
        markdown, data = self.reporter.generate(
            run,
            state.task,
            events,
            {
                "model_calls": len(self.repository.list_model_calls(run.id)),
                "tool_calls": len(self.repository.list_tool_calls(run.id)),
                "tool_failures": state.tool_failures,
                "tokens": state.tokens,
                "model_cost": state.model_cost,
                "duration_ms": duration_ms,
                "plan": state.plan.model_dump(mode="json") if state.plan else None,
                "verification": state.verification_summary,
                "completion_mode": self.profile.completion_mode,
                "validation_status": state.validation_status,
                "evidence_level": state.evidence_level,
                "final_answer": state.final_answer,
                "structured_output": state.structured_output,
                "context_tokens": state.context_tokens,
                "observation_chars": state.observation_chars,
                "context_truncations": state.context_truncations,
                "evidence_records": [
                    value.model_dump(mode="json") for value in self.repository.list_evidence(run.id)
                ],
            },
        )
        markdown = SafeTemplateRenderer.render(
            self.profile.report_template,
            {
                "task": state.task.body,
                "scenario": state.task.scenario,
                "thread_summary": "",
                "current_plan": state.plan.model_dump(mode="json") if state.plan else "",
                "observations": markdown,
                "remaining_budget": state.remaining_budget,
            },
        )
        self.repository.save_report(run.id, markdown, data)
        if self.profile.memory_policy.enabled:
            self.repository.save_memory(
                MemoryRecord(
                    thread_id=run.thread_id,
                    source_run_id=run.id,
                    kind="run_summary",
                    content=(state.final_answer or state.verification_summary)[:10_000],
                )
            )
        self.events.emit(
            run.id,
            EventType.RUN_COMPLETED,
            "运行完成，最终报告已生成",
            {"report_available": True},
        )
        return self._result("generate_report", state)

    def _route_action(self, raw: GraphState) -> str:
        state = self._state(raw)
        action = state.action
        if not action:
            return "fail"
        enabled = set(self.profile.workflow.nodes)
        if action.kind == "request_input":
            if str(state.task.mode) == "competition":
                target = self.profile.intervention_policy.competition_mode
                return target if target in enabled or target == "fail" else "fail"
            return (
                "request_input"
                if self.profile.intervention_policy.normal_mode == "wait"
                and "request_input" in enabled
                else "fail"
            )
        if action.kind == "call_tool" and not {
            "policy_check",
            "execute_tool",
            "observe",
        }.issubset(enabled):
            return "fail"
        if action.kind == "replan" and "replan" not in enabled:
            return "fail"
        return {
            "call_tool": "policy_check",
            "replan": "replan",
            "finish": "verify",
            "fail": "fail",
            "request_input": "request_input",
        }[action.kind]

    def _route_policy(self, raw: GraphState) -> str:
        action = self._state(raw).action
        return "replan" if action and action.kind == "replan" else "execute_tool"

    def _route_verify(self, raw: GraphState) -> str:
        return "complete" if self._state(raw).verified else "replan"

    def _route_observe(self, raw: GraphState) -> str:
        observations = self._state(raw).observations
        return "select_action" if observations and observations[-1].success else "replan"

    async def _fail(self, raw: GraphState) -> GraphState:
        state = self._state(raw)
        reason = state.action.summary if state.action else "模型未提供可执行动作"
        if state.action and state.action.kind == "request_input":
            reason = f"需要用户输入：{reason}"
        raise AgentDeclaredFailure(reason)

    def _build_graph(self, entry_point: str = "ingest") -> Any:
        graph = StateGraph(GraphState)
        add_node = cast(Any, graph.add_node)
        node_functions = [
            ("ingest", self._ingest),
            ("normalize_task", self._normalize_task),
            ("plan", self._plan),
            ("select_action", self._select_action),
            ("policy_check", self._policy_check),
            ("execute_tool", self._execute_tool),
            ("observe", self._observe),
            ("replan", self._replan),
            ("verify", self._verify),
            ("complete", self._complete),
            ("generate_report", self._generate_report),
            ("request_input", self._request_input),
            ("fail", self._fail),
        ]
        for name, function in node_functions:
            add_node(name, function)
        graph.set_entry_point(entry_point)
        graph.add_edge("ingest", "normalize_task")
        graph.add_edge(
            "normalize_task",
            "plan" if "plan" in self.profile.workflow.nodes else "select_action",
        )
        graph.add_edge("plan", "select_action")
        graph.add_conditional_edges(
            "select_action",
            self._route_action,
            {
                "policy_check": "policy_check",
                "replan": "replan",
                "verify": "verify",
                "request_input": "request_input",
                "fail": "fail",
            },
        )
        graph.add_conditional_edges(
            "policy_check",
            self._route_policy,
            {"replan": "replan", "execute_tool": "execute_tool"},
        )
        graph.add_edge("execute_tool", "observe")
        graph.add_conditional_edges(
            "observe",
            self._route_observe,
            {"select_action": "select_action", "replan": "replan"},
        )
        graph.add_edge("replan", "select_action")
        graph.add_conditional_edges(
            "verify", self._route_verify, {"complete": "complete", "replan": "replan"}
        )
        graph.add_edge("complete", "generate_report")
        graph.add_edge("generate_report", END)
        graph.add_edge("request_input", END)
        return graph.compile()

    async def run(
        self,
        run_id: UUID,
        task: TaskSpec,
        initial_state: AgentStateModel | None = None,
    ) -> None:
        run = self.repository.get_run(run_id)
        if not run:
            raise KeyError("运行不存在")
        run.transition(RunStatus.RUNNING)
        self.repository.save_run(run)
        self._last_tick[run.id] = time.monotonic()
        self.events.emit(
            run.id, EventType.RUN_STARTED, "Agent 运行已开始", {"provider": run.provider}
        )
        initial = initial_state or AgentStateModel(run_id=run.id, task=task)
        initial.run_id = run.id
        initial.task = task
        await self._invoke(run, task, initial, self.graph)

    async def resume(self, run_id: UUID, task: TaskSpec) -> None:
        run = self.repository.get_run(run_id)
        if not run:
            raise KeyError("运行不存在")
        checkpoint = self.repository.latest_checkpoint(run_id)
        if checkpoint is None:
            if run.status == RunStatus.QUEUED:
                await self.run(run_id, task)
                return
            await self._mark_recovery_failed(run, task, "运行缺少可恢复检查点")
            return
        state = AgentStateModel.model_validate(checkpoint.state)
        state.elapsed_seconds = checkpoint.elapsed_seconds
        self._last_tick[run.id] = time.monotonic()
        uncertain = [
            call
            for call in self.repository.list_tool_calls(run_id)
            if call.status == CallStatus.STARTED
        ]
        for call in uncertain:
            tool = self.registry.get(call.tool_name)
            if not tool.spec.idempotent:
                await self._mark_recovery_failed(
                    run,
                    task,
                    f"工具 {call.tool_name} 的执行结果不确定且非幂等，禁止自动重复",
                )
                return
            call.status = CallStatus.FAILED
            call.error = "服务中断；幂等调用将在恢复流程重新执行"
            self.repository.save_tool_call(call)
        target = self._resume_target(checkpoint.node, state)
        self.events.emit(
            run.id,
            EventType.STATUS_UPDATE,
            "已从持久化检查点恢复运行",
            {
                "checkpoint_sequence": checkpoint.checkpoint_sequence,
                "resume_node": target,
            },
        )
        await self._invoke(run, task, state, self._build_graph(target))

    async def _invoke(self, run: Any, task: TaskSpec, initial: AgentStateModel, graph: Any) -> None:
        try:
            await graph.ainvoke(initial.model_dump(mode="python"))
        except RunStopped as exc:
            run = self.repository.get_run(run.id) or run
            run.transition(RunStatus.STOPPED, str(exc))
            self.repository.save_run(run)
            self.events.emit(run.id, EventType.RUN_STOPPED, "运行已按请求安全停止")
        except asyncio.CancelledError:
            run = self.repository.get_run(run.id) or run
            if run.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
                run.transition(RunStatus.STOPPED, "用户请求停止并取消进行中的模型调用")
                self.repository.save_run(run)
                self.events.emit(run.id, EventType.RUN_STOPPED, "运行与进行中的模型请求已取消")
        except Exception as exc:
            run = self.repository.get_run(run.id) or run
            run.transition(RunStatus.FAILED, str(exc)[:500])
            self.repository.save_run(run)
            self.events.emit(run.id, EventType.RUN_FAILED, "运行安全终止", {"error": run.error})
            events = self.repository.list_events(run.id)
            markdown, data = self.reporter.generate(run, task, events, {})
            self.repository.save_report(run.id, markdown, data)
        finally:
            self._last_tick.pop(run.id, None)

    async def _mark_recovery_failed(self, run: Any, task: TaskSpec, reason: str) -> None:
        run.transition(RunStatus.FAILED, reason)
        self.repository.save_run(run)
        self.events.emit(run.id, EventType.RUN_FAILED, "恢复已安全终止", {"error": reason})
        markdown, data = self.reporter.generate(run, task, self.repository.list_events(run.id), {})
        self.repository.save_report(run.id, markdown, data)

    def _resume_target(self, node: str, state: AgentStateModel) -> str:
        if node == "select_action":
            return self._route_action(cast(GraphState, state.model_dump(mode="python")))
        if node == "policy_check":
            return self._route_policy(cast(GraphState, state.model_dump(mode="python")))
        if node == "observe":
            return self._route_observe(cast(GraphState, state.model_dump(mode="python")))
        if node == "verify":
            return self._route_verify(cast(GraphState, state.model_dump(mode="python")))
        mapping = {
            "ingest": "normalize_task",
            "normalize_task": "plan",
            "plan": "select_action",
            "execute_tool": "observe",
            "replan": "select_action",
            "complete": "generate_report",
            "fail": "fail",
            "request_input": "select_action",
            "input_received": "select_action",
        }
        try:
            return mapping[node]
        except KeyError as exc:
            raise AgentDeclaredFailure(f"未知恢复节点：{node}") from exc

    @staticmethod
    def _fingerprint(action: AgentAction) -> str:
        value = json.dumps(
            {
                "kind": action.kind,
                "tool": action.tool_name,
                "input": action.tool_input,
                "candidate": action.candidate.model_dump(mode="json") if action.candidate else None,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _observation_digest(observation: Observation) -> str:
        value = json.dumps(
            {
                "success": observation.success,
                "output": observation.output,
                "error": observation.error,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(value.encode()).hexdigest()

    def _track_plan_progress(self, state: AgentStateModel) -> None:
        if not state.plan:
            return
        fingerprint = hashlib.sha256(
            state.plan.model_dump_json().encode()
        ).hexdigest()
        repeats = state.plan_fingerprints.count(fingerprint)
        state.plan_fingerprints.append(fingerprint)
        if repeats >= 2:
            raise AgentDeclaredFailure("检测到循环规划，已安全终止")
