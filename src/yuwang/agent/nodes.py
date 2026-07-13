"""Agent 工作流节点与条件路由。

每个方法只推进一个可检查的状态步骤：读取状态、完成单一职责、发出事件，再由
Engine 写检查点。LangGraph 只负责编排这些普通异步函数，不拥有业务规则。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from jsonschema import ValidationError as JsonSchemaValidationError  # type: ignore[import-untyped]
from jsonschema import validate as validate_json_schema

from yuwang.agent.state import AgentDeclaredFailure, GraphState
from yuwang.domain.models import (
    AgentAction,
    AgentPlan,
    CallStatus,
    EventType,
    EvidenceRecord,
    Observation,
    RunStatus,
    ToolCall,
)

if TYPE_CHECKING:
    from yuwang.agent.engine import AgentEngine


class WorkflowNodes:
    """实现规划、动作、工具、观察、验证等单步状态转换。"""

    def __init__(self, engine: AgentEngine) -> None:
        self.engine = engine

    async def ingest(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        engine.events.emit(state.run_id, EventType.STATUS_UPDATE, "已载入不可变任务快照")
        return engine._result("ingest", state)

    async def normalize_task(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        state.task = state.task.model_copy(update={"body": state.task.body.strip()})
        engine.events.emit(
            state.run_id,
            EventType.STATUS_UPDATE,
            "任务已规范化",
            {"scenario": state.task.scenario},
        )
        return engine._result("normalize_task", state)

    async def plan(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        plan = await engine.planner.plan(state, cast(Any, engine._model_call))
        state.plan = AgentPlan.model_validate(plan)
        engine._track_plan_progress(state)
        engine.events.emit(
            state.run_id,
            EventType.PLAN_UPDATED,
            state.plan.summary,
            {"steps": state.plan.steps, "success_approach": state.plan.success_approach},
        )
        return engine._result("plan", state)

    async def select_action(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        action = await engine.action_selector.select(state, cast(Any, engine._model_call))
        state.action = AgentAction.model_validate(action)
        fingerprint = engine._fingerprint(state.action)
        repeats = state.action_fingerprints.count(fingerprint)
        state.action_fingerprints.append(fingerprint)
        if repeats >= 2:
            state.no_progress_count += 1
            state.action = AgentAction(kind="replan", summary="检测到重复动作，强制重新规划")
            engine.events.emit(
                state.run_id,
                EventType.WARNING,
                "检测到重复动作，已阻止再次执行",
            )
        if state.no_progress_count >= 3:
            raise AgentDeclaredFailure("连续无进展，已安全终止")
        engine.events.emit(
            state.run_id,
            EventType.STATUS_UPDATE,
            state.action.summary,
            {"action": state.action.kind},
        )
        return engine._result("select_action", state)

    async def policy_check(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        if not state.action or state.action.kind != "call_tool" or not state.action.tool_name:
            raise AgentDeclaredFailure("工具动作缺少必要字段")
        tool = engine.registry.get(state.action.tool_name)
        decision = engine.policy.check_tool(state.task, tool.spec, state.action.tool_input)
        engine.events.emit(
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
        return engine._result("policy_check", state)

    async def execute_tool(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        if not state.action or not state.action.tool_name:
            raise AgentDeclaredFailure("没有可执行工具动作")
        state.tool_calls += 1
        call_id = uuid4()
        engine.repository.save_tool_call(
            ToolCall(
                id=call_id,
                run_id=state.run_id,
                tool_name=state.action.tool_name,
                input_summary=state.action.summary,
                duration_ms=0,
                status=CallStatus.STARTED,
            )
        )
        engine.events.emit(
            state.run_id,
            EventType.TOOL_STARTED,
            f"开始调用 {state.action.tool_name}",
            {"call_id": str(call_id), "tool": state.action.tool_name},
        )
        result = await engine.executor.execute(
            state.action.tool_name,
            state.action.tool_input,
            state.task.budget.step_timeout_seconds,
        )
        if not result.success:
            state.tool_failures += 1
        engine.repository.save_tool_call(
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
        if state.observations and engine._observation_digest(
            state.observations[-1]
        ) == engine._observation_digest(observation):
            state.no_progress_count += 1
        else:
            state.no_progress_count = 0
        state.observations.append(observation)
        engine.events.emit(
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
        return engine._result("execute_tool", state)

    async def observe(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        latest = state.observations[-1]
        engine.events.emit(
            state.run_id,
            EventType.STATUS_UPDATE,
            "工具结果已作为不可信观察记录",
            {"call_id": str(latest.call_id), "success": latest.success},
        )
        return engine._result("observe", state)

    async def replan(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        state.replan_count += 1
        plan = await engine._model_call(state, AgentPlan, "根据历史观察重新规划")
        state.plan = AgentPlan.model_validate(plan)
        engine._track_plan_progress(state)
        engine.events.emit(
            state.run_id,
            EventType.REPLANNED,
            state.plan.summary,
            {"steps": state.plan.steps, "replan_count": state.replan_count},
        )
        return engine._result("replan", state)

    async def verify(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        if engine.profile.completion_mode == "advisory":
            if not state.action or not state.action.answer:
                raise AgentDeclaredFailure("建议回答模式缺少模型答案")
            state.verified = True
            state.validation_status = "unverified"
            state.evidence_level = "model"
            state.final_answer = state.action.answer
            state.verification_summary = "模型生成，未经外部验证"
            engine.events.emit(
                state.run_id,
                EventType.STATUS_UPDATE,
                state.verification_summary,
                {"verified": False, "evidence_level": "model"},
            )
            return engine._result("verify", state)
        if engine.profile.completion_mode == "structured":
            if not state.action or state.action.structured_output is None:
                raise AgentDeclaredFailure("结构化输出模式缺少输出对象")
            schema = engine.profile.validation_policy.json_schema
            if not schema:
                raise AgentDeclaredFailure("结构化输出模式未配置 JSON Schema")
            try:
                validate_json_schema(instance=state.action.structured_output, schema=schema)
            except JsonSchemaValidationError as exc:
                state.verification_summary = f"结构化输出校验失败：{exc.message[:200]}"
                return engine._result("verify", state)
            state.verified = True
            state.validation_status = "validated"
            state.evidence_level = "structured"
            state.structured_output = state.action.structured_output
            state.verification_summary = "结构化输出已通过配置的 JSON Schema 校验"
            return engine._result("verify", state)
        candidate = state.action.candidate if state.action else None
        result = engine.verifier.verify(state.task, candidate, state.observations)
        state.verified = result.verified
        state.validation_status = "validated" if result.verified else "pending"
        state.evidence_level = "external" if result.verified else "none"
        state.verification_summary = result.summary
        if candidate:
            engine.repository.save_evidence(
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
        engine.events.emit(
            state.run_id,
            EventType.STATUS_UPDATE,
            result.summary,
            {
                "verified": result.verified,
                "evidence_call_id": result.evidence_call_id,
                "rule_kind": result.rule_kind,
            },
        )
        return engine._result("verify", state)

    async def complete(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        if not state.verified:
            raise AgentDeclaredFailure("未通过确定性成功验证")
        engine.events.emit(state.run_id, EventType.STATUS_UPDATE, "验证通过，正在生成报告")
        return engine._result("complete", state)

    async def request_input(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        run = engine.repository.get_run(state.run_id)
        if not run:
            raise AgentDeclaredFailure("运行记录不存在")
        run.transition(RunStatus.WAITING_INPUT)
        engine.repository.save_run(run)
        engine.events.emit(
            state.run_id,
            EventType.RUN_WAITING_INPUT,
            state.action.summary if state.action else "等待用户补充信息",
            {"request_count": len(state.supplemental_inputs) + 1},
        )
        return engine._result("request_input", state)

    def route_action(self, raw: GraphState) -> str:
        engine = self.engine
        state = engine._state(raw)
        action = state.action
        if not action:
            return "fail"
        enabled = set(engine.profile.workflow.nodes)
        if action.kind == "request_input":
            if str(state.task.mode) == "competition":
                target = engine.profile.intervention_policy.competition_mode
                return target if target in enabled or target == "fail" else "fail"
            return (
                "request_input"
                if engine.profile.intervention_policy.normal_mode == "wait"
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

    def route_policy(self, raw: GraphState) -> str:
        engine = self.engine
        action = engine._state(raw).action
        if action and action.kind == "replan":
            return "replan" if "replan" in engine.profile.workflow.nodes else "fail"
        return "execute_tool"

    def route_verify(self, raw: GraphState) -> str:
        engine = self.engine
        if engine._state(raw).verified:
            return "complete"
        return "replan" if "replan" in engine.profile.workflow.nodes else "fail"

    def route_observe(self, raw: GraphState) -> str:
        engine = self.engine
        observations = engine._state(raw).observations
        if observations and observations[-1].success:
            return "select_action"
        return "replan" if "replan" in engine.profile.workflow.nodes else "fail"

    def should_plan(self) -> bool:
        """按明确规则决定新任务是否调用 Planner。"""

        profile = self.engine.profile
        if profile.planning_strategy == "direct":
            return False
        if profile.planning_strategy == "hybrid":
            return profile.completion_mode != "advisory"
        return True

    async def fail(self, raw: GraphState) -> GraphState:
        state = self.engine._state(raw)
        reason = state.action.summary if state.action else "模型未提供可执行动作"
        if state.action and state.action.kind == "request_input":
            reason = f"需要用户输入：{reason}"
        raise AgentDeclaredFailure(reason)
