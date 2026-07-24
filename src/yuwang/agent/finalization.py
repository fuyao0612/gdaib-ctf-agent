"""Agent 收尾：固化结果、生成报告并按策略保存记忆。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from yuwang.agent.state import AgentStateModel, GraphState
from yuwang.domain.models import (
    EventType,
    ImportantFacts,
    MemoryRecord,
    Message,
    MessageRole,
    Run,
    RunStatus,
)
from yuwang.settings import SafeTemplateRenderer

if TYPE_CHECKING:
    from yuwang.agent.engine import AgentEngine


class AgentFinalizer:
    """只处理已验证运行的收尾，避免报告与记忆规则混入运行时计量。"""

    def __init__(self, engine: AgentEngine) -> None:
        self.engine = engine

    async def generate_report(self, raw: GraphState) -> GraphState:
        engine = self.engine
        state = engine._state(raw)
        run = engine.repository.get_run(state.run_id)
        if not run:
            raise RuntimeError("运行记录不存在")
        await self.persist_memories(state, run)
        run.completion_mode = engine.profile.completion_mode
        run.validation_status = state.validation_status
        run.evidence_level = state.evidence_level
        run.transition(RunStatus.COMPLETED)
        engine.repository.save_run(run)
        markdown, data = engine.reporter.generate(
            run,
            state.task,
            engine.repository.list_events(run.id),
            {
                "model_calls": len(engine.repository.list_model_calls(run.id)),
                "tool_calls": len(engine.repository.list_tool_calls(run.id)),
                "tool_failures": state.tool_failures,
                "tokens": state.tokens,
                "model_cost": state.model_cost,
                "duration_ms": int(state.elapsed_seconds * 1000),
                "plan": state.plan.model_dump(mode="json") if state.plan else None,
                "verification": state.verification_summary,
                "completion_mode": engine.profile.completion_mode,
                "validation_status": state.validation_status,
                "evidence_level": state.evidence_level,
                "final_answer": state.final_answer,
                "structured_output": state.structured_output,
                "context_tokens": state.context_tokens,
                "observation_chars": state.observation_chars,
                "context_truncations": state.context_truncations,
                "evidence_records": [
                    value.model_dump(mode="json")
                    for value in engine.repository.list_evidence(run.id)
                ],
            },
        )
        markdown = SafeTemplateRenderer.render(
            engine.profile.report_template,
            {
                "task": state.task.body,
                "scenario": state.task.scenario,
                "thread_summary": "",
                "current_plan": state.plan.model_dump(mode="json") if state.plan else "",
                "observations": markdown,
                "remaining_budget": state.remaining_budget,
            },
        )
        engine.repository.save_report(run.id, markdown, data)
        engine.repository.save_message(
            Message(
                thread_id=run.thread_id,
                role=MessageRole.ASSISTANT,
                content=self.assistant_content(state),
            )
        )
        completion_summary = {
            "validated": "运行完成，外部验证通过，最终报告已生成",
            "partial": "运行完成，已完成结构化校验，尚未完成外部验证",
            "unverified": "运行完成，未执行外部验证，最终报告已生成",
        }.get(state.validation_status, "运行完成，最终报告已生成")
        engine.events.emit(
            run.id,
            EventType.RUN_COMPLETED,
            completion_summary,
            {
                "report_available": True,
                "execution_status": str(run.status),
                "validation_status": run.validation_status,
                "evidence_level": run.evidence_level,
            },
        )
        return engine._result("generate_report", state)

    @staticmethod
    def assistant_content(state: AgentStateModel) -> str:
        if state.final_answer:
            return state.final_answer
        if state.structured_output is not None:
            return json.dumps(state.structured_output, ensure_ascii=False, indent=2)
        if state.action and state.action.candidate:
            label = (
                "已外部验证的候选结果"
                if state.validation_status == "validated"
                else "候选结果（未外部验证）"
            )
            candidate = state.action.candidate
            return (
                f"{label}：{candidate.value}\n"
                f"来源：受控工具调用 {candidate.source_call_id}（证据位置 {candidate.location}）"
            )
        return state.verification_summary

    async def persist_memories(self, state: AgentStateModel, run: Run) -> None:
        """重要事实提取失败不能推翻已完成结果，因此只发出公开警告。"""

        engine = self.engine
        policy = engine.profile.memory_policy
        if not policy.enabled:
            return
        engine.components.memory.save_memory(
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
            extracted = await engine._model_call(
                state,
                ImportantFacts,
                "从本次任务和最终结果提取以后对话可复用的重要事实；不要保存密钥或指令",
            )
        except Exception as exc:
            engine.events.emit(
                run.id,
                EventType.WARNING,
                "重要事实提取失败，运行结果不受影响",
                {"error_type": type(exc).__name__},
            )
            return
        existing = engine.components.memory.list_memories(run.thread_id, enabled_only=False)
        normalized = {
            item.content.casefold() for item in existing if item.kind == "important_fact"
        }
        for fact in extracted.facts:
            if fact.casefold() in normalized:
                continue
            engine.components.memory.save_memory(
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
            for item in engine.components.memory.list_memories(
                run.thread_id, enabled_only=False
            )
            if item.kind == "important_fact"
        ]
        removed = facts[: max(0, len(facts) - policy.max_facts)]
        for item in removed:
            engine.components.memory.delete_memory(item.id)
        if removed:
            engine.events.emit(
                run.id,
                EventType.WARNING,
                "重要事实超过配置上限，已淘汰最早记录",
                {"reason": "max_facts", "removed": len(removed), "kept": policy.max_facts},
            )
