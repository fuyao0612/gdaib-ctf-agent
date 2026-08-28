"""LangGraph 装配、运行启动与检查点恢复。

工作流节点只描述“一步做什么”；本模块负责“从哪里开始、何时结束、异常如何
落库”。恢复时不会重放结果不确定的非幂等工具，这是最重要的安全边界之一。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from langgraph.graph import END, StateGraph

from yuwang.agent.failure_analysis import (
    FailureAnalysis,
    FailureAnalysisDraft,
    allows_model_failure_analysis,
    deterministic_failure_analysis,
    merge_model_failure_analysis,
)
from yuwang.agent.state import (
    AgentDeclaredFailure,
    AgentStateModel,
    GraphState,
    RunPaused,
    RunStopped,
)
from yuwang.domain.models import CallStatus, EventType, Run, RunStatus, TaskSpec
from yuwang.policy import redact
from yuwang.reports.trace import RunTraceService
from yuwang.results import TaskResultService

if TYPE_CHECKING:
    from yuwang.agent.engine import AgentEngine


class AgentRunCoordinator:
    """装配状态图并协调新运行、恢复、停止和失败报告。"""

    def __init__(self, engine: AgentEngine) -> None:
        self.engine = engine

    def _close_latest_step(self, run_id: UUID, reason: str) -> None:
        """终态没有下一次动作时，用真实终止原因收口最后一个公开步骤。"""

        for step in reversed(self.engine.repository.list_execution_steps(run_id)):
            if step.finished_at is not None and not step.decision:
                self.engine.repository.save_execution_step(
                    step.model_copy(update={"decision": redact(f"结束：{reason}")})
                )
                return

    def build_graph(self, entry_point: str = "ingest") -> Any:
        """根据 AgentProfile 的安全预设创建当前运行图。"""

        engine = self.engine
        graph = StateGraph(GraphState)
        add_node = cast(Any, graph.add_node)
        node_functions = [
            ("ingest", engine._ingest),
            ("create_task_brief", engine._create_task_brief),
            ("await_clarification", engine._await_clarification),
            ("normalize_task", engine._normalize_task),
            ("plan", engine._plan),
            ("await_plan_approval", engine._await_plan_approval),
            ("select_action", engine._select_action),
            ("policy_check", engine._policy_check),
            ("execute_tool", engine._execute_tool),
            ("observe", engine._observe),
            ("replan", engine._replan),
            ("verify", engine._verify),
            ("complete", engine._complete),
            ("generate_report", engine._generate_report),
            ("request_input", engine._request_input),
            ("fail", engine._fail),
        ]
        for name, function in node_functions:
            add_node(name, function)
        graph.set_entry_point(entry_point)
        graph.add_edge("ingest", "create_task_brief")
        graph.add_conditional_edges(
            "create_task_brief",
            engine._route_task_brief,
            {
                "await_clarification": "await_clarification",
                "normalize_task": "normalize_task",
            },
        )
        graph.add_edge("await_clarification", END)
        graph.add_conditional_edges(
            "normalize_task",
            engine._route_initial_planning,
            {"plan": "plan", "select_action": "select_action"},
        )
        graph.add_conditional_edges(
            "plan",
            engine._route_plan,
            {
                "await_plan_approval": "await_plan_approval",
                "select_action": "select_action",
            },
        )
        graph.add_edge("await_plan_approval", END)
        graph.add_conditional_edges(
            "select_action",
            engine._route_action,
            {
                "select_action": "select_action",
                "policy_check": "policy_check",
                "replan": "replan",
                "verify": "verify",
                "request_input": "request_input",
                "fail": "fail",
            },
        )
        graph.add_conditional_edges(
            "policy_check",
            engine._route_policy,
            {
                "select_action": "select_action",
                "replan": "replan",
                "execute_tool": "execute_tool",
                "await_plan_approval": "await_plan_approval",
                "fail": "fail",
            },
        )
        graph.add_edge("execute_tool", "observe")
        graph.add_conditional_edges(
            "observe",
            engine._route_observe,
            {"select_action": "select_action", "replan": "replan", "fail": "fail"},
        )
        graph.add_edge("replan", "select_action")
        graph.add_conditional_edges(
            "verify",
            engine._route_verify,
            {"complete": "complete", "replan": "replan", "fail": "fail"},
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
        """启动新 Run；`initial_state` 仅用于兼容安全重试，不会覆盖已持久化事实。"""

        """把队列中的 Run 转为运行中，并从图入口开始推进。"""

        engine = self.engine
        run = engine.repository.get_run(run_id)
        if not run:
            raise KeyError("运行不存在")
        run.transition(RunStatus.RUNNING)
        engine.repository.save_run(run)
        engine._last_tick[run.id] = time.monotonic()
        engine.events.emit(
            run.id,
            EventType.RUN_STARTED,
            "Agent 运行已开始",
            {"provider": run.provider},
        )
        initial = initial_state or AgentStateModel(run_id=run.id, task=task)
        initial.run_id = run.id
        initial.task = task
        await self.invoke(run, task, initial, engine.graph)

    async def resume(self, run_id: UUID, task: TaskSpec) -> None:
        """从最后检查点恢复，先处理未完成工具调用的不确定性。"""

        engine = self.engine
        run = engine.repository.get_run(run_id)
        if not run:
            raise KeyError("运行不存在")
        checkpoint = engine.repository.latest_checkpoint(run_id)
        if checkpoint is None:
            if run.status == RunStatus.QUEUED:
                await self.run(run_id, task)
                return
            await self.mark_recovery_failed(run, task, "运行缺少可恢复检查点")
            return
        state = AgentStateModel.model_validate(checkpoint.state)
        state.elapsed_seconds = checkpoint.elapsed_seconds
        engine._last_tick[run.id] = time.monotonic()
        uncertain = [
            call
            for call in engine.repository.list_tool_calls(run_id)
            if call.status == CallStatus.STARTED
        ]
        for call in uncertain:
            reference = call.tool_id or call.tool_name
            try:
                tool = engine.registry.get(reference)
            except KeyError:
                await self.mark_recovery_failed(
                    run,
                    task,
                    f"恢复运行所需工具 {reference} 未注册、已停用或当前不可用",
                )
                return
            if not tool.spec.idempotent:
                await self.mark_recovery_failed(
                    run,
                    task,
                    f"工具 {call.tool_name} 的执行结果不确定且非幂等，禁止自动重复",
                )
                return
            call.status = CallStatus.FAILED
            call.error = "服务中断；幂等调用将在恢复流程重新执行"
            engine.repository.save_tool_call(call)
        guidance = engine._apply_guidance(state)
        if guidance:
            # 暂停期间到达的指引必须先进入持久化状态，恢复后才能安全地直接重规划。
            engine.repository.commit_guidance_checkpoint(
                run_id=run_id,
                node=checkpoint.node,
                state=state.model_dump(mode="json"),
                guidance=guidance,
            )
        target = self.resume_target(checkpoint.node, state)
        engine.events.emit(
            run.id,
            EventType.STATUS_UPDATE,
            "已从持久化检查点恢复运行",
            {"checkpoint_sequence": checkpoint.checkpoint_sequence, "resume_node": target},
        )
        await self.invoke(run, task, state, self.build_graph(target))

    async def invoke(
        self,
        run: Run,
        task: TaskSpec,
        initial: AgentStateModel,
        graph: Any,
    ) -> None:
        """执行编译后的图，将控制异常转换为明确 Run 状态并始终清理计时状态。"""

        """统一收口停止、取消和失败，保证每种退出都有持久化结果。"""

        engine = self.engine
        try:
            await graph.ainvoke(initial.model_dump(mode="python"))
        except RunStopped as exc:
            run = engine.repository.get_run(run.id) or run
            run = self._restore_validation_snapshot(run)
            run.transition(RunStatus.STOPPED, str(exc))
            self._close_latest_step(run.id, str(exc))
            engine.repository.save_run(run)
            engine.events.emit(
                run.id,
                EventType.RUN_STOPPED,
                "运行已按请求安全停止",
                self._terminal_payload(run),
            )
        except RunPaused as exc:
            run = engine.repository.get_run(run.id) or run
            run = self._restore_validation_snapshot(run)
            run.transition(RunStatus.PAUSED, str(exc))
            self._close_latest_step(run.id, str(exc))
            engine.repository.save_run(run)
            engine.events.emit(
                run.id,
                EventType.RUN_PAUSED,
                "运行已在安全检查点暂停",
                self._terminal_payload(run),
            )
        except asyncio.CancelledError:
            run = engine.repository.get_run(run.id) or run
            run = self._restore_validation_snapshot(run)
            if run.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
                run.transition(RunStatus.STOPPED, "用户请求停止并取消进行中的模型调用")
                self._close_latest_step(run.id, run.error or "用户请求停止")
                engine.repository.save_run(run)
                engine.events.emit(
                    run.id,
                    EventType.RUN_STOPPED,
                    "运行与进行中的模型请求已取消",
                    self._terminal_payload(run),
                )
        except Exception as exc:
            run = engine.repository.get_run(run.id) or run
            run = self._restore_validation_snapshot(run)
            await self._persist_failure(run, task, exc, "运行安全终止")
        finally:
            engine._last_tick.pop(run.id, None)

    async def mark_recovery_failed(self, run: Run, task: TaskSpec, reason: str) -> None:
        """恢复条件不安全时生成可查看的失败报告，而不是静默丢失运行。"""

        run = self._restore_validation_snapshot(run)
        await self._persist_failure(run, task, reason, "恢复已安全终止")

    async def _persist_failure(
        self,
        run: Run,
        task: TaskSpec,
        error: BaseException | str,
        event_summary: str,
    ) -> None:
        """保存失败运行、可选复盘与报告；即使复盘失败也保留确定性原因。"""

        engine = self.engine
        fallback = deterministic_failure_analysis(error)
        run.transition(RunStatus.FAILED, fallback.summary[:500])
        self._close_latest_step(run.id, run.error or fallback.summary)
        engine.repository.save_run(run)
        checkpoint = engine.repository.latest_checkpoint(run.id)
        state = (
            AgentStateModel.model_validate(checkpoint.state)
            if checkpoint is not None
            else None
        )
        # 失败轨迹同样可能已经收集到候选和证据，不能因为终态变化而丢失。
        TaskResultService(engine.repository).persist(
            run,
            task,
            state.structured_output if state else None,
            final_answer=state.final_answer if state else None,
            validation_status="failed",
        )
        run = engine.repository.get_run(run.id) or run
        analysis = await self._failure_analysis(run, task, error, fallback)
        engine.events.emit(
            run.id,
            EventType.RUN_FAILED,
            event_summary,
            self._terminal_payload(
                run,
                error=run.error,
                failure_analysis=analysis.model_dump(mode="json"),
            ),
        )
        markdown, data = engine.reporter.generate(
            run,
            task,
            engine.repository.list_events(run.id),
            {
                "failure_analysis": analysis.model_dump(mode="json"),
                "trace": RunTraceService(engine.repository).snapshot(run.id),
            },
        )
        engine.repository.save_report(run.id, markdown, data)

    async def _failure_analysis(
        self,
        run: Run,
        task: TaskSpec,
        error: BaseException | str,
        fallback: FailureAnalysis,
    ) -> FailureAnalysis:
        """仅在安全且还有一次模型预算时请求面向用户的失败复盘。"""

        if not allows_model_failure_analysis(error):
            return fallback
        state = self._failure_analysis_state(run.id, task)
        if state.model_calls >= task.budget.max_model_calls:
            return fallback.model_copy(
                update={
                    "next_steps": [
                        *fallback.next_steps,
                        "模型调用预算已耗尽，未额外请求失败复盘。",
                    ][:4]
                }
            )
        recent_events = self.engine.repository.list_events(run.id)[-6:]
        event_summary = "；".join(event.summary[:180] for event in recent_events) or "尚无已持久化事件"
        purpose = (
            "生成面向用户的失败复盘：只输出简短结论、可能原因和下一步；"
            "不得输出隐藏思维过程、不得调用工具、不得把未验证信息写成成功。"
            f"确定性失败原因：{fallback.summary}。近期审计摘要：{event_summary}"
        )
        try:
            draft = await self.engine._model_call(
                state,
                FailureAnalysisDraft,
                purpose,
                request_budget=1,
            )
        except Exception:
            return fallback
        return merge_model_failure_analysis(fallback, draft)

    def _failure_analysis_state(self, run_id: UUID, task: TaskSpec) -> AgentStateModel:
        """从最后检查点和已持久化审计恢复预算，避免复盘绕过模型预算。"""

        checkpoint = self.engine.repository.latest_checkpoint(run_id)
        state = (
            AgentStateModel.model_validate(checkpoint.state)
            if checkpoint is not None
            else AgentStateModel(run_id=run_id, task=task)
        )
        state.run_id = run_id
        state.task = task
        calls = self.engine.repository.list_model_calls(run_id)
        state.model_calls = sum(
            max(0, int(call.metadata.get("request_count", 1)))
            if isinstance(call.metadata, dict)
            else 1
            for call in calls
        )
        state.tokens = sum(call.input_tokens + call.output_tokens for call in calls)
        return state

    def _restore_validation_snapshot(self, run: Run) -> Run:
        """异常收口前恢复最近检查点的验证结论，避免失败运行回退为 pending。"""

        checkpoint = self.engine.repository.latest_checkpoint(run.id)
        if checkpoint is None:
            return run
        state = AgentStateModel.model_validate(checkpoint.state)
        run.validation_status = state.validation_status
        run.evidence_level = state.evidence_level
        return run

    @staticmethod
    def _terminal_payload(run: Run, **details: Any) -> dict[str, Any]:
        """终态事件始终同时携带执行、验证和证据三个独立维度。"""

        return {
            "execution_status": str(run.status),
            "validation_status": run.validation_status,
            "evidence_level": run.evidence_level,
            **details,
        }

    def resume_target(self, node: str, state: AgentStateModel) -> str:
        """把“已完成节点”映射为下一安全节点，避免重放已发生副作用。"""

        engine = self.engine
        raw = cast(GraphState, state.model_dump(mode="python"))
        if node == "select_action":
            return engine._route_action(raw)
        if node == "policy_check":
            return engine._route_policy(raw)
        if node == "observe":
            return engine._route_observe(raw)
        if node == "verify":
            return engine._route_verify(raw)
        mapping = {
            "ingest": "create_task_brief",
            "create_task_brief": "normalize_task",
            "await_clarification": "create_task_brief",
            "clarification_received": "create_task_brief",
            "normalize_task": "plan",
            "plan": "select_action",
            "await_plan_approval": "select_action",
            "plan_edited": "await_plan_approval",
            "plan_approved": "select_action",
            "plan_rejected": "plan",
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
