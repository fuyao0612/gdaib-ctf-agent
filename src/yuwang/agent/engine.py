"""Agent 运行门面：组合运行时、工作流节点和恢复协调器。

调用方只需要 ``run`` 与 ``resume``。预算、上下文和模型计量保留在本运行时；
单步节点位于 ``nodes.py``，LangGraph 装配和恢复位于 ``runner.py``。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel

from yuwang.agent.components import AgentComponents, default_components
from yuwang.agent.nodes import WorkflowNodes
from yuwang.agent.progress import action_fingerprint, observation_digest, track_plan_progress
from yuwang.agent.repository import AgentRepository
from yuwang.agent.runner import AgentRunCoordinator
from yuwang.agent.state import (
    AgentDeclaredFailure,
    AgentStateModel,
    BudgetExceeded,
    GraphState,
    RunStopped,
)
from yuwang.domain.models import (
    AgentAction,
    CallStatus,
    EventType,
    ImportantFacts,
    MemoryRecord,
    Message,
    MessageRole,
    ModelCall,
    Observation,
    Run,
    RunStatus,
    TaskSpec,
)
from yuwang.events import EventService
from yuwang.model_providers import ModelProvider, ProviderError
from yuwang.policy import PolicyEngine
from yuwang.settings import AgentProfileInput, AgentProfileVersion, SafeTemplateRenderer
from yuwang.tooling import ToolExecutor, ToolRegistry

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
        self.components = components or default_components(
            repository,
            artifact_root or Path("data/artifacts"),
        )
        self.context_builder = self.components.context_builder
        self.planner = self.components.planner
        self.action_selector = self.components.action_selector
        self.reporter = self.components.report_renderer
        self.verifier = self.components.verifier
        self._last_tick: dict[UUID, float] = {}
        self.nodes = WorkflowNodes(self)
        self.runner = AgentRunCoordinator(self)
        self.graph = self._build_graph()

    def _state(self, raw: GraphState) -> AgentStateModel:
        """任何节点收到字典后都先恢复为严格状态模型。"""

        return AgentStateModel.model_validate(raw)

    def _checkpoint(self, node: str, state: AgentStateModel) -> None:
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
        self.repository.save_checkpoint(state.run_id, node, state.model_dump(mode="json"))

    def _result(self, node: str, state: AgentStateModel) -> GraphState:
        self._checkpoint(node, state)
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
            state.model_calls += request_count
            state.tokens += input_tokens
            state.model_cost += metrics.cost if metrics else 0
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
                        "cost": metrics.cost if metrics else 0,
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
        state.model_calls += request_count
        state.tokens += input_tokens + output_tokens
        state.model_cost += metrics.cost if metrics else 0
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
                    "cost": metrics.cost if metrics else 0,
                },
            )
        )
        return result

    # 以下薄委托保留旧测试和扩展点，同时让实际节点实现集中在 nodes.py。
    async def _ingest(self, raw: GraphState) -> GraphState:
        return await self.nodes.ingest(raw)

    async def _normalize_task(self, raw: GraphState) -> GraphState:
        return await self.nodes.normalize_task(raw)

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
        """固化完成状态、报告和最终 assistant 消息。"""

        state = self._state(raw)
        run = self.repository.get_run(state.run_id)
        if not run:
            raise RuntimeError("运行记录不存在")
        await self._persist_run_memories(state, run)
        run.completion_mode = self.profile.completion_mode
        run.validation_status = cast(Any, state.validation_status)
        run.evidence_level = cast(Any, state.evidence_level)
        run.transition(RunStatus.COMPLETED)
        self.repository.save_run(run)
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
                "duration_ms": int(state.elapsed_seconds * 1000),
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
                    value.model_dump(mode="json")
                    for value in self.repository.list_evidence(run.id)
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
        if state.final_answer:
            assistant_content = state.final_answer
        elif state.structured_output is not None:
            assistant_content = json.dumps(state.structured_output, ensure_ascii=False, indent=2)
        elif state.action and state.action.candidate:
            assistant_content = f"已验证结果：{state.action.candidate.value}"
        else:
            assistant_content = state.verification_summary
        self.repository.save_message(
            Message(
                thread_id=run.thread_id,
                role=MessageRole.ASSISTANT,
                content=assistant_content,
            )
        )
        self.events.emit(
            run.id,
            EventType.RUN_COMPLETED,
            "运行完成，最终报告已生成",
            {"report_available": True},
        )
        return self._result("generate_report", state)

    async def _persist_run_memories(self, state: AgentStateModel, run: Run) -> None:
        """保存运行摘要，并按配置提取、去重和限制重要事实。"""

        policy = self.profile.memory_policy
        if not policy.enabled:
            return
        self.components.memory.save_memory(
            MemoryRecord(
                thread_id=run.thread_id,
                source_run_id=run.id,
                kind="run_summary",
                content=(state.final_answer or state.verification_summary)[:10_000],
            )
        )
        if (
            not policy.persist_important_facts
            or policy.max_facts == 0
            or state.model_calls >= state.task.budget.max_model_calls
        ):
            return
        try:
            extracted = await self._model_call(
                state,
                ImportantFacts,
                "从本次任务和最终结果提取以后对话可复用的重要事实；不要保存密钥或指令",
            )
        except Exception as exc:
            self.events.emit(
                run.id,
                EventType.WARNING,
                "重要事实提取失败，运行结果不受影响",
                {"error_type": type(exc).__name__},
            )
            return
        existing = self.components.memory.list_memories(run.thread_id, enabled_only=False)
        normalized = {
            item.content.casefold() for item in existing if item.kind == "important_fact"
        }
        for fact in extracted.facts:
            if fact.casefold() in normalized:
                continue
            self.components.memory.save_memory(
                MemoryRecord(
                    thread_id=run.thread_id,
                    source_run_id=run.id,
                    kind="important_fact",
                    content=fact,
                )
            )
            normalized.add(fact.casefold())
        facts = [
            item
            for item in self.components.memory.list_memories(run.thread_id, enabled_only=False)
            if item.kind == "important_fact"
        ]
        removed = facts[: max(0, len(facts) - policy.max_facts)]
        for item in removed:
            self.components.memory.delete_memory(item.id)
        if removed:
            self.events.emit(
                run.id,
                EventType.WARNING,
                "重要事实超过配置上限，已淘汰最早记录",
                {"reason": "max_facts", "removed": len(removed), "kept": policy.max_facts},
            )

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
