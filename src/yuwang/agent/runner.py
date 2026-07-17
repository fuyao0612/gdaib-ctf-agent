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

from yuwang.agent.state import (
    AgentDeclaredFailure,
    AgentStateModel,
    GraphState,
    RunPaused,
    RunStopped,
)
from yuwang.domain.models import CallStatus, EventType, Run, RunStatus, TaskSpec

if TYPE_CHECKING:
    from yuwang.agent.engine import AgentEngine


class AgentRunCoordinator:
    """装配状态图并协调新运行、恢复、停止和失败报告。"""

    def __init__(self, engine: AgentEngine) -> None:
        self.engine = engine

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
            {"replan": "replan", "execute_tool": "execute_tool", "fail": "fail"},
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
            tool = engine.registry.get(call.tool_name)
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
        if engine._apply_guidance(state):
            # 暂停期间到达的指引必须先进入持久化状态，恢复后才能安全地直接重规划。
            engine.repository.save_checkpoint(
                run_id, checkpoint.node, state.model_dump(mode="json")
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
            run.transition(RunStatus.STOPPED, str(exc))
            engine.repository.save_run(run)
            engine.events.emit(run.id, EventType.RUN_STOPPED, "运行已按请求安全停止")
        except RunPaused as exc:
            run = engine.repository.get_run(run.id) or run
            run.transition(RunStatus.PAUSED, str(exc))
            engine.repository.save_run(run)
            engine.events.emit(run.id, EventType.RUN_PAUSED, "运行已在安全检查点暂停")
        except asyncio.CancelledError:
            run = engine.repository.get_run(run.id) or run
            if run.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
                run.transition(RunStatus.STOPPED, "用户请求停止并取消进行中的模型调用")
                engine.repository.save_run(run)
                engine.events.emit(
                    run.id,
                    EventType.RUN_STOPPED,
                    "运行与进行中的模型请求已取消",
                )
        except Exception as exc:
            run = engine.repository.get_run(run.id) or run
            run.transition(RunStatus.FAILED, str(exc)[:500])
            engine.repository.save_run(run)
            engine.events.emit(
                run.id,
                EventType.RUN_FAILED,
                "运行安全终止",
                {"error": run.error},
            )
            events = engine.repository.list_events(run.id)
            markdown, data = engine.reporter.generate(run, task, events, {})
            engine.repository.save_report(run.id, markdown, data)
        finally:
            engine._last_tick.pop(run.id, None)

    async def mark_recovery_failed(self, run: Run, task: TaskSpec, reason: str) -> None:
        """恢复条件不安全时生成可查看的失败报告，而不是静默丢失运行。"""

        engine = self.engine
        run.transition(RunStatus.FAILED, reason)
        engine.repository.save_run(run)
        engine.events.emit(run.id, EventType.RUN_FAILED, "恢复已安全终止", {"error": reason})
        markdown, data = engine.reporter.generate(
            run,
            task,
            engine.repository.list_events(run.id),
            {},
        )
        engine.repository.save_report(run.id, markdown, data)

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
