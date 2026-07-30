"""Agent 工作流节点与条件路由。

每个方法只推进一个可检查的状态步骤：读取状态、完成单一职责、发出事件，再由
Engine 写检查点。LangGraph 只负责编排这些普通异步函数，不拥有业务规则。
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from jsonschema import ValidationError as JsonSchemaValidationError  # type: ignore[import-untyped]
from jsonschema import validate as validate_json_schema

from yuwang.agent.state import AgentDeclaredFailure, GraphState
from yuwang.control import (
    PlanRevision,
    PlanSource,
    TaskBrief,
    TaskBriefDraft,
    TaskBriefSource,
)
from yuwang.domain.models import (
    AgentAction,
    AgentPlan,
    Artifact,
    CallStatus,
    EventType,
    EvidenceCandidate,
    EvidenceLevel,
    EvidenceRecord,
    ExecutionStep,
    Observation,
    RunStatus,
    ToolCall,
    ValidationStatus,
)
from yuwang.policy import redact, redact_data
from yuwang.reports.presentation import present_tool_observation
from yuwang.reports.trace import RunTraceService
from yuwang.tooling import ToolCallRequest, ToolProgress

if TYPE_CHECKING:
    from yuwang.agent.engine import AgentEngine


class WorkflowNodes:
    """实现规划、动作、工具、观察、验证等单步状态转换。"""

    def __init__(self, engine: AgentEngine) -> None:
        self.engine = engine

    @staticmethod
    def _public_action_reason(action: AgentAction) -> str:
        """仅保留短小的公开理由；服务端动作使用确定性回退文本。"""

        return redact(action.action_reason or "根据当前已持久化的计划和观察，执行该动作以继续收集可核对的事实。")[:600]

    def _link_previous_decision(self, state: Any, action: AgentAction) -> None:
        """将下一次已选择的公开动作关联到最近未收口的工具步骤。"""

        for step in reversed(self.engine.repository.list_execution_steps(state.run_id)):
            if step.finished_at is None or step.decision:
                continue
            if action.kind == "call_tool":
                decision = f"下一步：{action.summary}"
            elif action.kind == "replan":
                decision = f"重新规划：{action.summary}"
            elif action.kind == "request_input":
                decision = f"等待补充：{action.summary}"
            else:
                decision = f"结束：{action.summary}"
            self.engine.repository.save_execution_step(step.model_copy(update={"decision": redact(decision)}))
            return

    def _archive_large_tool_output(
        self,
        state: Any,
        call_id: UUID,
        output: dict[str, Any],
        summary: str,
    ) -> tuple[dict[str, Any], list[UUID]]:
        """超出上下文预算的工具输出写入 Artifact，检查点仅保留可追溯引用。"""

        serialized = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
        if len(serialized) <= 8_000:
            return output, []
        run = self.engine.repository.get_run(state.run_id)
        if not run:
            return output, []
        content = serialized.encode("utf-8")
        storage_ref = f"{run.thread_id}/tool-output-{run.id}-{call_id}.json"
        destination = self.engine.artifact_root / storage_ref
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        artifact = self.engine.repository.save_artifact(
            Artifact(
                thread_id=run.thread_id,
                run_id=run.id,
                filename=f"tool-output-{call_id}.json",
                kind="tool_output",
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
                mime_type="application/json",
                storage_ref=storage_ref,
            )
        )
        self.engine.events.emit(
            state.run_id,
            EventType.ARTIFACT_CREATED,
            "工具长输出已归档为 Artifact",
            {"artifact_id": str(artifact.id), "call_id": str(call_id), "size": artifact.size},
        )
        return (
            {
                "artifact_id": str(artifact.id),
                "summary": summary,
                "original_chars": len(serialized),
                "content_in_artifact": True,
            },
            [artifact.id],
        )

    async def ingest(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        engine.events.emit(state.run_id, EventType.STATUS_UPDATE, "已载入不可变任务快照")
        return engine._result("ingest", state)

    async def create_task_brief(self, raw: GraphState) -> GraphState:
        """用正式 Provider 生成公开 Task Brief，服务端负责不可伪造的版本字段。"""

        engine = self.engine
        state = engine._state(raw)
        if engine.profile.planning_strategy == "direct":
            # Avoid an unconditional model call before the first action.  The
            # original task remains in the immutable TaskSpec and context.
            engine.events.emit(
                state.run_id,
                EventType.STATUS_UPDATE,
                "直接执行模式：已保留原始任务上下文",
                {"task_brief": "bypassed", "reason": "direct_workflow"},
            )
            return engine._result("create_task_brief", state)
        draft = await engine._model_call(
            state,
            TaskBriefDraft,
            "生成公开 Task Brief；信息不足时只提出必要澄清问题，不输出隐藏思维链",
        )
        previous = engine.repository.latest_task_brief(state.run_id)
        brief = TaskBrief(
            run_id=state.run_id,
            version=1 if previous is None else previous.version + 1,
            original_request=state.task.body,
            source=(
                TaskBriefSource.AGENT
                if previous is None
                else TaskBriefSource.USER_CLARIFICATION
            ),
            **draft.model_dump(),
        )
        engine.repository.save_task_brief(brief)
        state.task_brief = brief
        engine.events.emit(
            state.run_id,
            EventType.TASK_BRIEF_CREATED,
            "Task Brief 已生成" if previous is None else "Task Brief 已根据补充更新",
            {
                "version": brief.version,
                "needs_clarification": brief.needs_clarification,
                "question_count": len(brief.clarification_questions),
            },
        )
        return engine._result("create_task_brief", state)

    async def await_clarification(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        if not state.task_brief or not state.task_brief.needs_clarification:
            raise AgentDeclaredFailure("Task Brief 未要求澄清")
        run = engine.repository.get_run(state.run_id)
        if not run:
            raise AgentDeclaredFailure("运行记录不存在")
        run.transition(RunStatus.WAITING_CLARIFICATION)
        engine.repository.save_run(run)
        engine.events.emit(
            state.run_id,
            EventType.CLARIFICATION_REQUESTED,
            "任务信息不足，等待用户补充",
            {
                "brief_version": state.task_brief.version,
                "question_count": len(state.task_brief.clarification_questions),
            },
        )
        return engine._result("await_clarification", state)

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
        previous = engine.repository.latest_plan_revision(state.run_id)
        revision = PlanRevision(
            run_id=state.run_id,
            version=1 if previous is None else previous.version + 1,
            plan=state.plan,
            source=(PlanSource.AGENT_INITIAL if previous is None else PlanSource.AGENT_REPLAN),
            based_on_version=previous.version if previous else None,
        )
        engine.repository.save_plan_revision(revision)
        engine._track_plan_progress(state)
        engine.events.emit(
            state.run_id,
            EventType.PLAN_UPDATED,
            state.plan.summary,
            {"steps": state.plan.steps, "success_approach": state.plan.success_approach},
        )
        engine.events.emit(
            state.run_id,
            EventType.PLAN_CREATED,
            "执行计划已生成" if previous is None else "执行计划已重新生成",
            {"version": revision.version, "source": str(revision.source)},
        )
        return engine._result("plan", state)

    async def await_plan_approval(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        if not state.plan:
            raise AgentDeclaredFailure("没有可确认的计划")
        run = engine.repository.get_run(state.run_id)
        if not run:
            raise AgentDeclaredFailure("运行记录不存在")
        run.transition(RunStatus.WAITING_APPROVAL)
        engine.repository.save_run(run)
        revision = engine.repository.latest_plan_revision(state.run_id)
        if state.pending_risk_approval_tool:
            engine.events.emit(
                state.run_id,
                EventType.RISK_APPROVAL_REQUESTED,
                f"中风险工具“{state.pending_risk_approval_tool}”等待用户确认",
                {
                    "plan_version": revision.version if revision else None,
                    "tool": state.pending_risk_approval_tool,
                    "risk": "medium",
                },
            )
        else:
            engine.events.emit(
                state.run_id,
                EventType.PLAN_APPROVAL_REQUESTED,
                "计划等待用户确认",
                {"plan_version": revision.version if revision else None},
            )
        return engine._result("await_plan_approval", state)

    async def select_action(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        action = await engine.select_action(state)
        state.action = AgentAction.model_validate(action)
        self._link_previous_decision(state, state.action)
        fingerprint = engine._fingerprint(state.action)
        repeats = state.action_fingerprints.count(fingerprint)
        state.action_fingerprints.append(fingerprint)
        if repeats >= 2:
            state.no_progress_count += 1
            state.action = AgentAction(
                kind="replan", summary="检测到重复动作，强制重新规划",
                action_reason="当前动作与已执行动作重复，改为重新规划以避免重复调用。",
            )
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
        tool_id = engine.run_tool_id(state.task, state.action.tool_name)
        if tool_id is None:
            reason = "工具不在本次 Run 的允许快照中，已拒绝执行"
            engine.events.emit(
                state.run_id,
                EventType.POLICY_CHECKED,
                reason,
                {"allowed": False, "tool": state.action.tool_name, "reason": "run_tool_snapshot"},
            )
            state.observations.append(
                Observation(
                    call_id=uuid4(),
                    tool_name=state.action.tool_name,
                    success=False,
                    summary="Run 工具快照拒绝工具动作",
                    error=reason,
                )
            )
            state.action = AgentAction(
                kind="replan", summary="工具快照拒绝后重新规划",
                action_reason="该工具不在当前 Run 的持久化允许快照中，需要重新规划。",
            )
            return engine._result("policy_check", state)
        try:
            tool = engine.registry.get(tool_id)
        except KeyError:
            reason = "Run 工具快照中的工具当前不可用，已拒绝执行"
            engine.events.emit(
                state.run_id,
                EventType.POLICY_CHECKED,
                reason,
                {"allowed": False, "tool": state.action.tool_name, "reason": "tool_unavailable"},
            )
            state.observations.append(
                Observation(
                    call_id=uuid4(),
                    tool_name=state.action.tool_name,
                    success=False,
                    summary="Run 工具快照中的工具不可用",
                    error=reason,
                )
            )
            state.action = AgentAction(
                kind="replan", summary="工具不可用后重新规划",
                action_reason="当前 Run 已记录的工具不可用，需要重新规划可执行路径。",
            )
            return engine._result("policy_check", state)
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
            state.action = AgentAction(
                kind="replan", summary="策略拒绝后重新规划",
                action_reason="当前动作未通过策略检查，需要重新规划合规的后续步骤。",
            )
        elif decision.requires_approval:
            fingerprint = engine._fingerprint(state.action)
            if state.approved_risk_action_fingerprint == fingerprint:
                return engine._result("policy_check", state)
            # 中风险工具不创建 ToolCall，更不触发执行；审批信息与当前检查点一同持久化。
            state.pending_risk_approval_tool = tool.spec.name
            state.pending_risk_approval_fingerprint = fingerprint
            engine.events.emit(
                state.run_id,
                EventType.STATUS_UPDATE,
                decision.reason,
                {"tool": tool.spec.name, "risk": "medium", "requires_approval": True},
            )
        return engine._result("policy_check", state)

    async def execute_tool(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        if not state.action or not state.action.tool_name:
            raise AgentDeclaredFailure("没有可执行工具动作")
        tool_id = engine.run_tool_id(state.task, state.action.tool_name)
        if tool_id is None:
            raise AgentDeclaredFailure("工具不在本次 Run 的允许快照中，拒绝执行")
        try:
            tool = engine.registry.get(tool_id)
        except KeyError as exc:
            raise AgentDeclaredFailure("Run 工具快照中的工具当前不可用，拒绝执行") from exc
        state.tool_calls += 1
        call_id = uuid4()
        request = ToolCallRequest(
            call_id=call_id,
            run_id=state.run_id,
            tool_id=tool.spec.id,
            tool_version=tool.spec.version,
            arguments=state.action.tool_input,
            target_scope=state.task.authorized_targets,
            approval_fingerprint=state.approved_risk_action_fingerprint,
        )
        # 全局工作流计数不等于计划步骤序号；没有持久化的可靠关联时，直接使用
        # 当前动作的公开摘要作为目标，避免把多个工具错误映射到计划最后一步。
        goal = state.action.summary
        public_arguments = redact_data(state.action.tool_input)
        assert isinstance(public_arguments, dict)
        # ToolCall 负责完整审计；ExecutionStep 只保存脱敏后的公开叙述，页面刷新后
        # 仍能以稳定 call_id 原位更新同一张行动/观察卡片。
        engine.repository.save_execution_step(
            ExecutionStep(
                run_id=state.run_id,
                sequence=engine.repository.next_execution_step_sequence(state.run_id),
                call_id=call_id,
                goal=redact(goal),
                action_kind="tool_call",
                action_summary=redact(state.action.summary),
                action_reason=self._public_action_reason(state.action),
                tool_id=tool.spec.id,
                tool_name=tool.spec.display_name,
                arguments=public_arguments,
            )
        )
        engine.repository.save_tool_call(
            ToolCall(
                id=call_id,
                run_id=state.run_id,
                tool_name=state.action.tool_name,
                tool_id=request.tool_id,
                tool_version=request.tool_version,
                arguments=request.arguments,
                target_scope=request.target_scope,
                approval_fingerprint=request.approval_fingerprint,
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
        async def report_progress(progress: ToolProgress) -> None:
            engine.events.emit(
                state.run_id,
                EventType.TOOL_PROGRESS,
                progress.message,
                {
                    "call_id": str(progress.call_id),
                    "percent": progress.percent,
                    "reported_at": progress.reported_at.isoformat(),
                },
            )

        result = await engine.executor.execute_call(
            request,
            state.task.budget.step_timeout_seconds,
            progress_reporter=report_progress,
        )
        if not result.success:
            state.tool_failures += 1
        output, generated_artifact_ids = self._archive_large_tool_output(
            state,
            call_id,
            result.output,
            result.summary,
        )
        artifact_ids = [UUID(value) for value in result.artifact_ids]
        artifact_ids.extend(generated_artifact_ids)
        for artifact_id in result.artifact_ids:
            engine.events.emit(
                state.run_id,
                EventType.ARTIFACT_CREATED,
                "工具已生成派生 Artifact",
                {"artifact_id": artifact_id, "call_id": str(call_id)},
            )
        presentation = present_tool_observation(
            tool.spec.id,
            success=result.success,
            output=output,
            error=result.error.message if result.error else None,
            artifact_count=len(artifact_ids),
        )
        engine.repository.save_tool_call(
            ToolCall(
                id=call_id,
                run_id=state.run_id,
                tool_name=state.action.tool_name,
                tool_id=result.executed_tool_id,
                tool_version=result.executed_tool_version,
                arguments=request.arguments,
                target_scope=request.target_scope,
                approval_fingerprint=request.approval_fingerprint,
                input_summary=state.action.summary,
                result_summary=result.summary,
                duration_ms=result.duration_ms,
                status=CallStatus.SUCCEEDED if result.success else CallStatus.FAILED,
                error=result.error.message if result.error else None,
                artifact_ids=artifact_ids,
            )
        )
        observation = Observation(
            call_id=call_id,
            tool_name=state.action.tool_name,
            success=result.success,
            output=output,
            summary=presentation.summary,
            error=result.error.message if result.error else None,
        )
        if state.observations and engine._observation_digest(
            state.observations[-1]
        ) == engine._observation_digest(observation):
            state.no_progress_count += 1
        else:
            state.no_progress_count = 0
        state.observations.append(observation)
        # Flag 格式检查只会产生“候选”证据，不会把格式匹配误报成赛题平台验证成功。
        candidate = result.structured_output.get("candidate")
        validation = result.structured_output.get("validation_status")
        evidence_ids: list[UUID] = []
        if result.success and tool.spec.id in {"ctf.flag_candidate_verify", "ctf.encoding_decode"}:
            structured_candidates = (
                [candidate]
                if isinstance(candidate, str)
                else result.structured_output.get("candidates", [])
            )
            if not isinstance(structured_candidates, list):
                structured_candidates = []
            for candidate_index, candidate_value in enumerate(structured_candidates):
                candidate_text = (
                    candidate_value.get("value")
                    if isinstance(candidate_value, dict)
                    else candidate_value
                )
                if not isinstance(candidate_text, str) or not candidate_text.strip():
                    continue
                is_format_tool = tool.spec.id == "ctf.flag_candidate_verify"
                evidence = EvidenceRecord(
                    run_id=state.run_id,
                    candidate=candidate_text.strip(),
                    source_call_id=call_id,
                    location=("/candidate" if is_format_tool else f"/candidates/{candidate_index}/value"),
                    verified=False,
                    verification_summary=(
                        "候选 Flag 已通过格式校验，尚未进行确定性或平台验证"
                        if is_format_tool and validation == "format_matched"
                        else "工具输出发现候选值，尚未执行格式、确定性或平台验证"
                    ),
                    rule_kind="flag_format" if is_format_tool else None,
                    discovery_source=("tool_call" if is_format_tool else "encoding_decode"),
                    format_status=("format_matched" if is_format_tool and validation == "format_matched" else "not_checked"),
                    verification_scope=("format" if is_format_tool and validation == "format_matched" else "none"),
                    deterministic_validation_status="not_run",
                    platform_validation_status="not_run",
                )
                engine.repository.save_evidence(evidence)
                evidence_ids.append(evidence.id)
        current_step = engine.repository.get_execution_step_by_call(state.run_id, call_id)
        if current_step:
            observation_status = (
                "success" if result.success else "timeout" if result.timed_out else "stopped"
                if result.cancelled else "error"
            )
            engine.repository.save_execution_step(
                current_step.model_copy(
                    update={
                        "observation_status": observation_status,
                        "observation_summary": presentation.summary,
                        "observation_facts": presentation.facts,
                        "observation_details": presentation.status_details,
                        "reproduction_hint": presentation.reproduction_hint,
                        "preview": RunTraceService.preview(output, result.summary),
                        "error": redact(result.error.message) if result.error else None,
                        "artifact_ids": artifact_ids,
                        "evidence_ids": evidence_ids,
                        "finished_at": result.finished_at,
                        "duration_ms": result.duration_ms,
                    }
                )
            )
        engine.events.emit(
            state.run_id,
            EventType.TOOL_FINISHED,
            presentation.summary,
            {
                "call_id": str(call_id),
                "tool": state.action.tool_name,
                "success": result.success,
                "duration_ms": result.duration_ms,
                "error": result.error.model_dump() if result.error else None,
                "artifact_ids": [str(value) for value in artifact_ids],
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
            latest.summary,
            {"call_id": str(latest.call_id), "success": latest.success},
        )
        return engine._result("observe", state)

    async def replan(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        # 只有已应用指引明确要求重规划时，才把其序号写入事件载荷。这个持久化
        # 关联让客户端能准确显示“因本指引重规划”，无需从时间戳推断因果。
        guidance_sequences = (
            list(state.guidance_replan_sequences)
            if state.guidance_replan_required
            else []
        )
        state.guidance_replan_required = False
        state.guidance_replan_sequences.clear()
        state.replan_count += 1
        if engine.profile.planning_strategy == "direct":
            # 直接模式的“重新判断”只表示下一次 select_action 必须读取最新观察或
            # 用户指引。额外生成计划既不会增加安全边界，也会无谓多消耗一次模型调用。
            payload: dict[str, Any] = {
                "replan_count": state.replan_count,
                "planning_strategy": "direct",
            }
            if guidance_sequences:
                payload["guidance_sequences"] = guidance_sequences
            engine.events.emit(
                state.run_id,
                EventType.REPLANNED,
                "直接模式已基于最新上下文重新选择下一动作",
                payload,
            )
            return engine._result("replan", state)
        state.plan = await engine.planner.plan(
            state,
            cast(Any, engine._model_call),
        )
        previous = engine.repository.latest_plan_revision(state.run_id)
        revision = PlanRevision(
            run_id=state.run_id,
            version=1 if previous is None else previous.version + 1,
            plan=state.plan,
            source=PlanSource.AGENT_REPLAN,
            based_on_version=previous.version if previous else None,
        )
        engine.repository.save_plan_revision(revision)
        engine._track_plan_progress(state)
        payload = {"steps": state.plan.steps, "replan_count": state.replan_count}
        if guidance_sequences:
            payload["guidance_sequences"] = guidance_sequences
        engine.events.emit(
            state.run_id,
            EventType.REPLANNED,
            state.plan.summary,
            payload,
        )
        return engine._result("replan", state)

    def route_task_brief(self, raw: GraphState) -> str:
        brief = self.engine._state(raw).task_brief
        return "await_clarification" if brief and brief.needs_clarification else "normalize_task"

    def route_plan(self, raw: GraphState) -> str:
        state = self.engine._state(raw)
        run = self.engine.repository.get_run(state.run_id)
        if run and run.plan_mode == "approval" and not state.plan_approved:
            return "await_plan_approval"
        return "select_action"

    def route_initial_planning(self, raw: GraphState) -> str:
        """旧自动执行保留直接策略；计划确认模式始终生成可审核计划。"""

        state = self.engine._state(raw)
        run = self.engine.repository.get_run(state.run_id)
        if run and run.plan_mode == "approval":
            return "plan"
        return "plan" if self.should_plan() else "select_action"

    def _record_validation(
        self,
        state: Any,
        *,
        validation_status: ValidationStatus,
        evidence_level: EvidenceLevel,
        summary: str,
        completion_ready: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        """同步验证结论，避免 Run、事件和检查点表达不同的可信度。"""

        engine = self.engine
        state.validation_status = validation_status
        state.evidence_level = evidence_level
        state.verification_summary = summary
        state.completion_ready = completion_ready
        run = engine.repository.get_run(state.run_id)
        if run:
            run.validation_status = validation_status
            run.evidence_level = evidence_level
            engine.repository.save_run(run)
        engine.events.emit(
            state.run_id,
            EventType.STATUS_UPDATE,
            summary,
            {
                "execution_status": str(run.status) if run else RunStatus.RUNNING.value,
                "validation_status": validation_status,
                "evidence_level": evidence_level,
                "completion_ready": completion_ready,
                **(details or {}),
            },
        )

    @staticmethod
    def _candidate_evidence_level(state: Any, candidate: Any) -> EvidenceLevel:
        """候选值只有关联到成功工具调用时才算外部证据。"""

        if candidate is None:
            return "none"
        if any(
            item.call_id == candidate.source_call_id and item.success
            for item in state.observations
        ):
            return "external"
        return "model"

    @staticmethod
    def _latest_flag_candidate(state: Any) -> EvidenceCandidate | None:
        """收尾缺少候选引用时，仅复用专用工具已验证格式的真实输出。"""

        for observation in reversed(state.observations):
            output = observation.output
            candidates = output.get("candidates")
            if observation.success and observation.tool_name == "ctf.encoding_decode" and isinstance(candidates, list):
                for index, item in enumerate(candidates):
                    value = item.get("value") if isinstance(item, dict) else item
                    if isinstance(value, str) and value.strip():
                        return EvidenceCandidate(
                            value=value.strip(), source_call_id=observation.call_id,
                            location=f"/candidates/{index}/value",
                        )
            if (
                observation.success
                and observation.tool_name == "ctf.flag_candidate_verify"
                and output.get("validation_status") == "format_matched"
                and isinstance(output.get("candidate"), str)
            ):
                return EvidenceCandidate(
                    value=output["candidate"],
                    source_call_id=observation.call_id,
                    location="/candidate",
                )
        return None

    async def verify(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        if engine.profile.completion_mode == "advisory":
            if not state.action or not state.action.answer:
                raise AgentDeclaredFailure("建议回答模式缺少模型答案")
            state.final_answer = state.action.answer
            self._record_validation(
                state,
                validation_status="unverified",
                evidence_level="model",
                summary="模型生成，未执行外部验证",
                completion_ready=True,
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
                self._record_validation(
                    state,
                    validation_status="failed",
                    evidence_level="model",
                    summary=f"结构化输出校验失败：{exc.message[:200]}",
                    completion_ready=False,
                )
                return engine._result("verify", state)
            # JSON Schema 只能证明模型输出的形状符合约束，不能证明任务结论
            # 已被外部系统验证。运行仍可完成，但界面必须保持“部分验证”。
            state.structured_output = state.action.structured_output
            self._record_validation(
                state,
                validation_status="partial",
                evidence_level="structured",
                summary="结构化输出已通过 JSON Schema 校验，未完成外部验证",
                completion_ready=True,
            )
            return engine._result("verify", state)
        candidate = state.action.candidate if state.action else None
        candidate = candidate or self._latest_flag_candidate(state)
        if not state.task.verification_rules:
            # 任务可以给出可用结论，但没有确定性外部条件时绝不声称“已验证成功”。
            if not state.action or not (state.action.answer or candidate):
                raise AgentDeclaredFailure("未配置验证条件且模型未提供可展示结论")
            # 候选值交给收尾层按“候选、来源、验证等级”统一呈现；不能只把
            # 一个 Flag 样式字符串当作最终已确认答案输出。
            state.final_answer = state.action.answer
            self._record_validation(
                state,
                validation_status="unverified",
                evidence_level=(
                    self._candidate_evidence_level(state, candidate)
                    if candidate
                    else "model"
                ),
                summary="未配置确定性外部验证条件，结果未外部验证",
                completion_ready=True,
            )
            return engine._result("verify", state)
        result = engine.verifier.verify(state.task, candidate, state.observations)
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
        self._record_validation(
            state,
            validation_status="validated" if result.verified else "failed",
            evidence_level=(
                "external"
                if result.verified
                else self._candidate_evidence_level(state, candidate)
            ),
            summary=result.summary,
            completion_ready=result.verified,
            details={
                "evidence_call_id": result.evidence_call_id,
                "rule_kind": result.rule_kind,
            },
        )
        return engine._result("verify", state)

    async def complete(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        if not state.completion_ready:
            raise AgentDeclaredFailure("未通过确定性成功验证")
        summary = {
            "validated": "外部验证通过，正在生成报告",
            "partial": "结构化校验已完成，外部验证尚未完成，正在生成报告",
            "unverified": "未执行外部验证，正在生成报告",
        }.get(state.validation_status, "正在生成报告")
        run = engine.repository.get_run(state.run_id)
        engine.events.emit(
            state.run_id,
            EventType.STATUS_UPDATE,
            summary,
            {
                "execution_status": str(run.status) if run else RunStatus.RUNNING.value,
                "validation_status": state.validation_status,
                "evidence_level": state.evidence_level,
                "completion_ready": True,
            },
        )
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
        if state.guidance_replan_required:
            return self._adjustment_target(state)
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
            return "select_action" if engine.profile.planning_strategy == "direct" else "fail"
        return {
            "call_tool": "policy_check",
            "replan": "replan",
            "finish": "verify",
            "fail": "fail",
            "request_input": "request_input",
        }[action.kind]

    def route_policy(self, raw: GraphState) -> str:
        engine = self.engine
        state = engine._state(raw)
        if state.guidance_replan_required:
            return self._adjustment_target(state)
        if state.pending_risk_approval_tool:
            return "await_plan_approval"
        action = state.action
        if action and action.kind == "replan":
            return self._adjustment_target(state)
        return "execute_tool"

    def route_verify(self, raw: GraphState) -> str:
        engine = self.engine
        state = engine._state(raw)
        if state.guidance_replan_required:
            return "replan" if "replan" in engine.profile.workflow.nodes else "fail"
        if state.completion_ready:
            return "complete"
        return "replan" if "replan" in engine.profile.workflow.nodes else "fail"

    def route_observe(self, raw: GraphState) -> str:
        engine = self.engine
        state = engine._state(raw)
        if state.guidance_replan_required:
            return self._adjustment_target(state)
        observations = state.observations
        if observations and observations[-1].success:
            return "select_action"
        return self._adjustment_target(state)

    def _adjustment_target(self, state: Any) -> str:
        """Keep direct mode in its same Agent loop after a failed observation."""

        if "replan" in self.engine.profile.workflow.nodes:
            return "replan"
        if self.engine.profile.planning_strategy == "direct":
            return "select_action"
        return "fail"

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
