"""Agent 运行门面：组合运行时、工作流节点和恢复协调器。

调用方只需要 ``run`` 与 ``resume``。预算、上下文和模型计量保留在本运行时；
单步节点位于 ``nodes.py``，LangGraph 装配和恢复位于 ``runner.py``。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError

from yuwang.agent.components import AgentComponents, default_components
from yuwang.agent.finalization import AgentFinalizer
from yuwang.agent.nodes import WorkflowNodes
from yuwang.agent.progress import action_fingerprint, observation_digest, track_plan_progress
from yuwang.agent.repository import AgentRepository
from yuwang.agent.runner import AgentRunCoordinator
from yuwang.agent.state import (
    AgentDeclaredFailure,
    AgentStateModel,
    BudgetExceeded,
    GraphState,
    RunPaused,
    RunStopped,
)
from yuwang.control import RunGuidance
from yuwang.domain.models import (
    AgentAction,
    CallStatus,
    EventType,
    ModelCall,
    Observation,
    Run,
    TaskSpec,
)
from yuwang.events import EventService
from yuwang.model_providers import ModelProvider, ProviderCallMetrics, ProviderError
from yuwang.policy import PolicyEngine
from yuwang.settings import AgentProfileInput, AgentProfileVersion
from yuwang.tooling import ToolExecutor, ToolRegistry
from yuwang.tooling.adapters.function_calling import FunctionToolCatalog

ModelT = TypeVar("ModelT", bound=BaseModel)


class AgentEngine:
    """Provider 与工具实现无关的 Agent 门面，完成权始终由验证器掌握。"""

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
            **AgentProfileInput(name="默认安全 Agent").model_dump(),
            version=1,
        )
        self.artifact_root = (artifact_root or Path("data/artifacts")).resolve()
        self.components = components or default_components(repository, self.artifact_root)
        self.context_builder = self.components.context_builder
        self.planner = self.components.planner
        self.action_selector = self.components.action_selector
        self.reporter = self.components.report_renderer
        self.verifier = self.components.verifier
        self._last_tick: dict[UUID, float] = {}
        self.nodes = WorkflowNodes(self)
        self.finalizer = AgentFinalizer(self)
        self.runner = AgentRunCoordinator(self)
        self.graph = self._build_graph()

    def _state(self, raw: GraphState) -> AgentStateModel:
        """任何节点收到字典后都先恢复为严格状态模型。"""

        return AgentStateModel.model_validate(raw)

    def _checkpoint(
        self,
        node: str,
        state: AgentStateModel,
        applied_guidance: list[RunGuidance] | None = None,
    ) -> None:
        """累计真实消耗、检查全部预算并写入追加式检查点。"""

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
        checkpoint_state = state.model_dump(mode="json")
        if applied_guidance:
            self.repository.commit_guidance_checkpoint(
                run_id=state.run_id,
                node=node,
                state=checkpoint_state,
                guidance=applied_guidance,
            )
            return
        self.repository.save_checkpoint(state.run_id, node, checkpoint_state)

    def _apply_guidance(self, state: AgentStateModel) -> list[RunGuidance]:
        """先把指引并入内存状态，随后由检查点事务一次性落库。"""

        guidance = self.repository.list_pending_guidance(state.run_id)
        if not guidance:
            return []
        state.supplemental_inputs.extend(item.content for item in guidance)
        for item in guidance:
            for artifact_id in item.artifact_ids:
                if artifact_id not in state.supplemental_artifact_ids:
                    state.supplemental_artifact_ids.append(artifact_id)
        # 人工介入带来了新信息，介入前的重复指纹不再代表当前路径无进展。
        state.action_fingerprints.clear()
        state.plan_fingerprints.clear()
        state.no_progress_count = 0
        state.guidance_replan_required = True
        for item in guidance:
            if item.sequence not in state.guidance_replan_sequences:
                state.guidance_replan_sequences.append(item.sequence)
        return guidance

    def _result(self, node: str, state: AgentStateModel) -> GraphState:
        safe_nodes = {"select_action", "policy_check", "observe", "verify", "replan"}
        guidance = self._apply_guidance(state) if node in safe_nodes else []
        self._checkpoint(node, state, guidance)
        if node in safe_nodes and self.repository.consume_pause_request(state.run_id):
            raise RunPaused("已在安全检查点暂停")
        return cast(GraphState, state.model_dump(mode="python"))

    def _context(self, state: AgentStateModel, purpose: str) -> str:
        """构建带锚点和剩余预算的上下文，检测任务或配置被意外替换。"""

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
        # 新 Run 使用已固化的工具快照；旧 Run 没有快照时才兼容读取注册表。
        tool_schemas = (
            [snapshot.model_dump(mode="json") for snapshot in state.task.tool_snapshots]
            if state.task.tool_snapshots
            else [spec.model_dump(mode="json") for spec in self.registry.specs()]
        )
        state.tool_schemas = (
            tool_schemas if getattr(self.provider, "tool_call_mode", "structured") != "disabled" else []
        )
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
                {
                    "reasons": result.reasons,
                    "estimated_tokens": result.estimated_tokens,
                    "messages": {
                        "original": result.original_message_count,
                        "kept": result.kept_message_count,
                    },
                    "memories": {
                        "original": result.original_memory_count,
                        "kept": result.kept_memory_count,
                    },
                },
            )
        return result.prompt

    async def select_action(self, state: AgentStateModel) -> AgentAction:
        """按 Run 已固化的 Provider 模式选择动作，不在失败后切换调用协议。"""

        tool_call_mode = str(getattr(self.provider, "tool_call_mode", "structured"))
        if tool_call_mode != "native":
            action = await self.action_selector.select(state, cast(Any, self._model_call))
            if tool_call_mode == "disabled" and action.kind == "call_tool":
                return AgentAction(
                    kind="fail",
                    summary="当前 Provider 已禁用工具调用，不能执行模型请求的工具动作",
                )
            return action
        if not state.task.tool_snapshots:
            raise AgentDeclaredFailure("原生 Function Calling 需要 Run 工具快照")
        prompt = self._context(state, "使用原生 Function Calling 选择下一动作")
        catalog = FunctionToolCatalog.from_snapshots(state.task.tool_snapshots)
        started = time.perf_counter()
        try:
            selection = await asyncio.wait_for(
                self.provider.generate_native_tool_selection(
                    prompt,
                    catalog,
                    timeout=state.task.budget.step_timeout_seconds,
                ),
                timeout=state.task.budget.step_timeout_seconds,
            )
        except Exception as exc:
            category = exc.category if isinstance(exc, ProviderError) else "timeout"
            metrics = getattr(exc, "metrics", None)
            self._record_native_tool_selection(
                state, "failed", category, metrics, started
            )
            raise
        metrics = getattr(self.provider, "last_call_metrics", None)
        self._record_native_tool_selection(state, "succeeded", None, metrics, started)
        if selection.invocation:
            return AgentAction(
                kind="call_tool",
                summary=f"原生 Function Calling 选择 {selection.invocation.tool_id}",
                tool_name=selection.invocation.tool_id,
                tool_input=selection.invocation.arguments,
            )
        if not selection.content:
            raise AgentDeclaredFailure("原生 Function Calling 未返回工具调用或受控动作")
        try:
            action = AgentAction.model_validate_json(selection.content)
        except ValidationError as exc:
            raise AgentDeclaredFailure("原生 Function Calling 的非工具动作不是合法 JSON") from exc
        if action.kind == "call_tool":
            raise AgentDeclaredFailure("原生 Function Calling 未通过 tool_calls 返回工具请求")
        return action

    def _record_native_tool_selection(
        self,
        state: AgentStateModel,
        status: CallStatus | str,
        category: str | None,
        metrics: ProviderCallMetrics | None,
        started: float,
    ) -> None:
        request_count = metrics.request_count if metrics else 1
        input_tokens = metrics.input_tokens if metrics else 0
        output_tokens = metrics.output_tokens if metrics else 0
        state.model_calls += request_count
        state.tokens += (metrics.total_tokens if metrics else 0)
        state.model_cost += metrics.cost if metrics else 0
        self.repository.save_model_call(
            ModelCall(
                run_id=state.run_id,
                provider=metrics.provider if metrics else self.provider.name,
                model=metrics.model if metrics else str(getattr(self.provider, "model", "unknown")),
                duration_ms=metrics.duration_ms if metrics else int((time.perf_counter() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                status=CallStatus(status),
                error_category=category,
                metadata={"purpose": "native_tool_selection", "tool_call_mode": "native"},
            )
        )

    async def _model_call(
        self,
        state: AgentStateModel,
        output_type: type[ModelT],
        purpose: str,
    ) -> ModelT:
        """调用结构化模型并把重试、Token、费用和错误分类完整计入审计。"""

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
            cost = self._model_cost(metrics, input_tokens, 0)
            state.model_calls += request_count
            state.tokens += input_tokens
            state.model_cost += cost
            self.repository.save_model_call(
                ModelCall(
                    run_id=state.run_id,
                    provider=metrics.provider if metrics else self.provider.name,
                    model=(
                        metrics.model
                        if metrics
                        else str(getattr(self.provider, "model", "provider-chain"))
                    ),
                    duration_ms=(
                        metrics.duration_ms
                        if metrics
                        else int((time.perf_counter() - started) * 1000)
                    ),
                    input_tokens=input_tokens,
                    output_tokens=0,
                    status=CallStatus.FAILED,
                    error_category=str(category),
                    metadata={
                        "purpose": purpose,
                        "request_count": request_count,
                        "retry_count": metrics.retry_count if metrics else 0,
                        "usage_reported": metrics.usage_reported if metrics else False,
                        "cost": cost,
                        "cost_estimated": not bool(metrics and metrics.usage_reported),
                    },
                )
            )
            raise
        metrics = getattr(self.provider, "last_call_metrics", None)
        request_count = metrics.request_count if metrics else 1
        input_tokens = (
            metrics.input_tokens
            if metrics and metrics.usage_reported
            else estimated_input_tokens
        )
        output_tokens = (
            metrics.output_tokens
            if metrics and metrics.usage_reported
            else max(1, len(result.model_dump_json()) // 4)
        )
        cost = self._model_cost(metrics, input_tokens, output_tokens)
        state.model_calls += request_count
        state.tokens += input_tokens + output_tokens
        state.model_cost += cost
        self.repository.save_model_call(
            ModelCall(
                run_id=state.run_id,
                provider=metrics.provider if metrics else self.provider.name,
                model=(
                    metrics.model
                    if metrics
                    else str(getattr(self.provider, "model", "provider-chain"))
                ),
                duration_ms=(
                    metrics.duration_ms
                    if metrics
                    else int((time.perf_counter() - started) * 1000)
                ),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                status=CallStatus.SUCCEEDED,
                metadata={
                    "purpose": purpose,
                    "request_count": request_count,
                    "retry_count": metrics.retry_count if metrics else 0,
                    "usage_reported": metrics.usage_reported if metrics else False,
                    "total_tokens": (
                        metrics.total_tokens if metrics else input_tokens + output_tokens
                    ),
                    "cost": cost,
                    "cost_estimated": not bool(metrics and metrics.usage_reported),
                },
            )
        )
        return result

    @staticmethod
    def _model_cost(
        metrics: ProviderCallMetrics | None, input_tokens: int, output_tokens: int
    ) -> float:
        """厂商未返回 usage 时按已配置单价和本地 Token 估算，不伪装成账单。"""

        if metrics and metrics.usage_reported:
            return metrics.cost
        if not metrics:
            return 0
        return (
            input_tokens * metrics.input_price_per_million
            + output_tokens * metrics.output_price_per_million
        ) / 1_000_000

    # 以下薄委托保留旧测试和扩展点，同时让实际节点实现集中在 nodes.py。
    async def _ingest(self, raw: GraphState) -> GraphState:
        return await self.nodes.ingest(raw)

    async def _normalize_task(self, raw: GraphState) -> GraphState:
        return await self.nodes.normalize_task(raw)

    async def _create_task_brief(self, raw: GraphState) -> GraphState:
        return await self.nodes.create_task_brief(raw)

    async def _await_clarification(self, raw: GraphState) -> GraphState:
        return await self.nodes.await_clarification(raw)

    async def _await_plan_approval(self, raw: GraphState) -> GraphState:
        return await self.nodes.await_plan_approval(raw)

    def _route_task_brief(self, raw: GraphState) -> str:
        return self.nodes.route_task_brief(raw)

    def _route_plan(self, raw: GraphState) -> str:
        return self.nodes.route_plan(raw)

    def _route_initial_planning(self, raw: GraphState) -> str:
        return self.nodes.route_initial_planning(raw)

    async def _plan(self, raw: GraphState) -> GraphState:
        return await self.nodes.plan(raw)

    async def _select_action(self, raw: GraphState) -> GraphState:
        return await self.nodes.select_action(raw)

    async def _policy_check(self, raw: GraphState) -> GraphState:
        return await self.nodes.policy_check(raw)

    async def _execute_tool(self, raw: GraphState) -> GraphState:
        return await self.nodes.execute_tool(raw)

    async def _observe(self, raw: GraphState) -> GraphState:
        return await self.nodes.observe(raw)

    async def _replan(self, raw: GraphState) -> GraphState:
        return await self.nodes.replan(raw)

    async def _verify(self, raw: GraphState) -> GraphState:
        return await self.nodes.verify(raw)

    async def _complete(self, raw: GraphState) -> GraphState:
        return await self.nodes.complete(raw)

    async def _request_input(self, raw: GraphState) -> GraphState:
        return await self.nodes.request_input(raw)

    async def _generate_report(self, raw: GraphState) -> GraphState:
        return await self.finalizer.generate_report(raw)

    def _route_action(self, raw: GraphState) -> str:
        return self.nodes.route_action(raw)

    def _route_policy(self, raw: GraphState) -> str:
        return self.nodes.route_policy(raw)

    def _route_verify(self, raw: GraphState) -> str:
        return self.nodes.route_verify(raw)

    def _route_observe(self, raw: GraphState) -> str:
        return self.nodes.route_observe(raw)

    def _should_plan(self) -> bool:
        return self.nodes.should_plan()

    async def _fail(self, raw: GraphState) -> GraphState:
        return await self.nodes.fail(raw)

    def _build_graph(self, entry_point: str = "ingest") -> Any:
        return self.runner.build_graph(entry_point)

    async def run(
        self,
        run_id: UUID,
        task: TaskSpec,
        initial_state: AgentStateModel | None = None,
    ) -> None:
        await self.runner.run(run_id, task, initial_state)

    async def resume(self, run_id: UUID, task: TaskSpec) -> None:
        await self.runner.resume(run_id, task)

    async def _invoke(
        self,
        run: Run,
        task: TaskSpec,
        initial: AgentStateModel,
        graph: Any,
    ) -> None:
        await self.runner.invoke(run, task, initial, graph)

    async def _mark_recovery_failed(self, run: Run, task: TaskSpec, reason: str) -> None:
        await self.runner.mark_recovery_failed(run, task, reason)

    def _resume_target(self, node: str, state: AgentStateModel) -> str:
        return self.runner.resume_target(node, state)

    @staticmethod
    def _fingerprint(action: AgentAction) -> str:
        return action_fingerprint(action)

    @staticmethod
    def _observation_digest(observation: Observation) -> str:
        return observation_digest(observation)

    def _track_plan_progress(self, state: AgentStateModel) -> None:
        track_plan_progress(state)


__all__ = [
    "AgentDeclaredFailure",
    "AgentEngine",
    "AgentStateModel",
    "BudgetExceeded",
]
