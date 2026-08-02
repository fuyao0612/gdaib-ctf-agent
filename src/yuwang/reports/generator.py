"""从持久化事实生成 Markdown 和 JSON 报告。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from yuwang.agent.retrospective import RunRetrospective, deterministic_retrospective
from yuwang.domain.models import Event, EvidenceReference, Run, TaskResult, TaskSpec
from yuwang.policy import redact, redact_data
from yuwang.reports.facts import ReportFacts


def _clean_decision(value: object) -> str | None:
    text = str(value or "").strip()
    for prefix in ("下一步：", "结束："):
        while text.startswith(prefix):
            text = text.removeprefix(prefix).strip()
    return text or None


def trust_notice(validation_status: str) -> str:
    return {
        "pending": "验证尚未完成，不能视为验证通过",
        "unverified": "结果未经外部验证",
        "partial": "已完成部分校验，尚未完成外部验证",
        "validated": "结果已通过确定性验证",
        "failed": "验证失败，结果不能视为已验证成功",
    }.get(validation_status, "验证状态未知，不能视为验证通过")


def completion_summary(validation_status: str) -> str:
    return {
        "pending": "执行已结束，但验证状态尚未确认",
        "unverified": "执行已结束，结果未经外部验证",
        "partial": "执行已结束，已完成部分校验但未完成外部验证",
        "validated": "执行已结束，结果已通过确定性验证",
        "failed": "执行已结束，但验证失败",
    }.get(validation_status, "执行已结束，验证状态未知")


def display_value(value: object) -> str:
    """用户可见 Markdown 只展示中文状态，不泄露协议机器值。"""

    return {
        "completed": "已完成",
        "failed": "失败",
        "stopped": "已停止",
        "not_checked": "未检查",
        "not_run": "未执行",
        "format_matched": "格式匹配",
        "format_failed": "格式不匹配",
        "passed": "通过",
        "encoding_decode": "编码解码",
        "tool_call": "工具调用",
    }.get(str(value), str(value))


def _uuid_values(values: list[dict[str, Any]]) -> list[UUID]:
    result: list[UUID] = []
    for item in values:
        raw = item.get("call_id")
        if not raw:
            continue
        try:
            result.append(UUID(str(raw)))
        except ValueError:
            continue
    return result


def _step_number(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 1 else None


def deterministic_conclusion(facts: ReportFacts) -> str:
    """最终结论只组合已持久化的答案、候选和验证状态。"""

    result = facts.final_answer
    if not result and facts.candidates:
        result = f"发现候选 {facts.candidates[0]['candidate']}"
    if not result:
        result = "未记录可展示的最终答案"
    return f"{result}；{trust_notice(facts.validation_status)}。"


class ReportGenerator:
    """唯一报告路径：ReportFacts.build -> _data -> _markdown。"""

    def generate(
        self, run: Run, task: TaskSpec, events: list[Event], metrics: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        facts = ReportFacts.build(run, task, events, metrics)
        data = self._data(run, task, facts, metrics)
        return self._markdown(facts, data), data

    @staticmethod
    def _data(
        run: Run, task: TaskSpec, facts: ReportFacts, metrics: dict[str, Any]
    ) -> dict[str, Any]:
        model = facts.metrics
        evidence_level = (
            "external"
            if any(item.get("source_call_id") for item in facts.candidates)
            else metrics.get("evidence_level", run.evidence_level)
        )
        raw_trace = metrics.get("trace")
        raw_plan = metrics.get("plan")
        trace: dict[str, Any] = raw_trace if isinstance(raw_trace, dict) else {}
        plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
        retrospective = metrics.get("retrospective")
        if not isinstance(retrospective, dict):
            failure = metrics.get("failure_analysis")
            reason = (
                "失败运行未完成模型复盘，以下内容由已持久化事实和失败分析确定性生成。"
                if isinstance(failure, dict)
                else "历史报告未记录模型复盘，以下内容由已持久化事实确定性生成。"
            )
            retrospective = deterministic_retrospective(
                facts, reason
            ).model_dump(mode="json")
        else:
            retrospective = RunRetrospective.model_validate(retrospective).model_dump(mode="json")
        result_type = "flag" if facts.report_kind == "ctf" and facts.candidates else "assessment"
        evidence_refs = [
            EvidenceReference(
                evidence_type=str(item.get("rule_kind") or item.get("source_kind") or "observation"),
                source=str(item.get("source_call_id") or item.get("source_kind") or "persisted-run"),
                content_summary=str(item.get("verification_summary") or item.get("candidate") or "持久化证据"),
                raw_ref=str(item.get("location") or item.get("source_call_id") or "run"),
                source_step=_step_number(item.get("source_step")),
                reliable=bool(item.get("verified") or item.get("platform_verified")),
                tool_verified=bool(item.get("source_call_id")),
            )
            for item in facts.candidates
        ]
        confidence = {
            "validated": 1.0,
            "partial": 0.6,
            "unverified": 0.25,
            "pending": 0.0,
            "failed": 0.0,
        }.get(facts.validation_status, 0.0)
        # New runs must persist their result before this report is finalized.  The fallback keeps
        # historical rows readable without modifying their immutable JSON payloads.
        task_result = run.results[0] if run.results else TaskResult(
            result_type=result_type,
            title="CTF Flag 结果" if result_type == "flag" else "通用安全任务结果",
            summary=deterministic_conclusion(facts),
            structured_data={"candidates": facts.candidates} if facts.candidates else {},
            scenario=facts.report_kind,
            evidence=evidence_refs,
            validation_status=facts.validation_status,
            validator_name="deterministic-report-facts",
            validator_version="1.0",
            validated_at=run.finished_at if facts.validation_status == "validated" else None,
            validation_explanation=facts.validation_label,
            confidence=confidence,
            source_steps=[
                number for item in facts.timeline
                if (number := _step_number(item.get("sequence"))) is not None
            ],
            tool_call_ids=_uuid_values(facts.timeline),
        )
        data = {
            "schema_version": "3.0",
            "report_kind": facts.report_kind,
            "report_kind_reason": facts.report_kind_reason,
            "run_id": str(run.id),
            "task_summary": facts.task_summary,
            "mode": str(task.mode),
            "execution_status": facts.execution_status,
            "status": facts.execution_status,
            "validation_status": facts.validation_status,
            "validation_label": facts.validation_label,
            "trust_notice": trust_notice(facts.validation_status),
            "evidence_level": evidence_level,
            "completion_mode": metrics.get("completion_mode", run.completion_mode),
            "result": completion_summary(facts.validation_status),
            "plan": plan.get("steps", []),
            "execution_mode": trace.get("execution_mode", "计划执行"),
            "final_answer": facts.final_answer,
            "structured_output": metrics.get("structured_output"),
            "flag_candidates": facts.candidates,
            "timeline": facts.timeline,
            "steps": facts.timeline,
            "key_clues": facts.key_clues,
            "evidence": [item["summary"] for item in facts.key_clues if item.get("summary")],
            "verification_evidence": metrics.get("evidence_records", []),
            "artifacts": facts.artifacts,
            "reproduction_steps": facts.reproduction_steps,
            "failed_attempts": facts.failed_attempts,
            "policy_summary": facts.policy_summary,
            "policy_checks": [item["summary"] for item in facts.policy_summary],
            "adjustments": facts.adjustments,
            "metrics": model,
            "duration_ms": model.get("duration_ms", metrics.get("duration_ms", 0)),
            "errors": [run.error] if run.error else [],
            "model_metrics": {
                "calls": model.get("logical_model_calls", metrics.get("model_calls", 0)),
                "logical_model_calls": model.get("logical_model_calls", metrics.get("model_calls", 0)),
                "provider_requests": model.get("provider_requests", metrics.get("model_calls", 0)),
                "input_tokens": model.get("input_tokens", 0),
                "output_tokens": model.get("output_tokens", 0),
                "tokens": model.get("total_tokens", metrics.get("tokens", 0)),
            },
            "tool_metrics": {
                "calls": model.get("tool_calls", metrics.get("tool_calls", 0)),
                "failures": model.get("tool_failures", metrics.get("tool_failures", 0)),
            },
            "limitations": [trust_notice(facts.validation_status)],
            "failure_analysis": metrics.get("failure_analysis"),
            "retrospective": retrospective,
            "handoff_summary": facts.handoff,
            "task_result": task_result.model_dump(mode="json"),
        }
        safe = redact_data(data)
        assert isinstance(safe, dict)
        return safe

    @staticmethod
    def _markdown(facts: ReportFacts, data: dict[str, Any]) -> str:
        title = "# 御网智元 CTF 解题报告" if facts.report_kind == "ctf" else "# 御网智元运行报告"
        lines = [title, "", "## 任务与最终结论"]
        lines.extend([
            f"- 执行状态：{display_value(facts.execution_status)}",
            f"- 验证状态：{facts.validation_label}",
            f"- 任务：{facts.task_summary}",
            f"- 最终结论：{deterministic_conclusion(facts)}",
        ])
        if facts.report_kind == "ctf":
            lines.extend(["", "## Flag 候选与验证状态"])
            lines.extend([
                f"- 候选 Flag：`{item['candidate']}`；格式校验：{display_value(item['format_status'])}；"
                f"确定性验证：{display_value(item['deterministic_validation_status'])}；"
                f"赛题平台验证：{display_value(item['platform_validation_status'])}；来源：{display_value(item['discovery_source'])}"
                for item in facts.candidates
            ] or ["- 未发现 Flag 候选。"])
        handoff = data.get("handoff_summary", {})
        if isinstance(handoff, dict):
            lines.extend([
                "", "## 人机交接摘要",
                f"- 当前目标：{handoff.get('current_goal', '未记录')}",
                f"- 已完成步骤：{', '.join(str(item) for item in handoff.get('completed_steps', [])) or '无'}",
                f"- 已验证结果：{', '.join(str(item) for item in handoff.get('validated_results', [])) or '无'}",
                f"- 当前阻塞：{'；'.join(handoff.get('current_blockers', [])) or '无'}",
                f"- 待审批事项：{'；'.join(handoff.get('pending_approvals', [])) or '无'}",
                f"- 建议接手动作：{handoff.get('recommended_action', '复核证据')}",
            ])
        retrospective = data["retrospective"]
        source_label = "模型复盘" if retrospective.get("source") == "model" else "确定性摘要"
        lines.extend(["", f"## 全过程复盘（{source_label}）"])
        lines.extend([
            f"- 总体总结：{retrospective.get('summary')}",
            f"- 结果复核：{retrospective.get('outcome_review')}",
            "- 有效做法：" + "；".join(retrospective.get("effective_actions") or ["未记录"]),
            "- 无效尝试：" + "；".join(retrospective.get("failed_attempts") or ["未记录"]),
            "- 经验：" + "；".join(retrospective.get("lessons") or ["未记录"]),
            "- 下一步建议：" + "；".join(retrospective.get("next_steps") or ["未记录"]),
        ])
        lines.extend(["", "## 关键线索"])
        lines.extend([f"- {item['summary']}" for item in facts.key_clues[:5]] or ["- 尚无可公开的关键观察。"])
        lines.extend(["", "## 详细执行过程"])
        for step in facts.timeline:
            decision = _clean_decision(step.get("decision")) or "运行在此步骤结束，未记录后续公开动作"
            lines.extend([
                f"### 步骤 {step.get('sequence')}",
                f"- 目标：{step.get('goal')}",
                f"- 理由：{step.get('action_reason') or '历史步骤未记录公开理由'}",
                f"- 行动：{step.get('action_summary')}",
                f"- 关键观察：{step.get('observation_summary') or '无'}",
                f"- 决策：{decision}",
                f"- 调用：`{step.get('call_id')}`",
            ])
        lines.extend(["", "## 可复现步骤"])
        lines.extend([ReportGenerator._reproduction_markdown(item) for item in facts.reproduction_steps])
        if facts.failed_attempts:
            lines.extend(["", "## 失败尝试"])
            lines.extend([
                f"- 步骤 {step.get('sequence')}：{step.get('observation_summary') or step.get('error') or '执行未成功'}"
                for step in facts.failed_attempts
            ])
        lines.extend(["", "## Artifact 清单"])
        lines.extend([
            f"- {item.get('filename')}；{item.get('mime_type')}；{item.get('size')} B；"
            f"来源步骤：{item.get('source_step') or '未记录'}；source_call_id：{item.get('source_call_id') or '未记录'}；"
            f"下载：{item.get('download_url')}"
            for item in facts.artifacts
        ] or ["- 无关联 Artifact。"])
        lines.extend([
            "", "## 资源消耗与审计",
            f"- 逻辑模型调用：{data['model_metrics']['logical_model_calls']}，"
            f"实际 Provider 请求：{data['model_metrics']['provider_requests']}；"
            f"Token：{data['model_metrics']['tokens']}",
            f"- 工具调用：{data['tool_metrics']['calls']}；失败：{data['tool_metrics']['failures']}",
        ])
        if facts.execution_status == "failed":
            analysis = data.get("failure_analysis")
            summary = analysis.get("summary") if isinstance(analysis, dict) else None
            lines.extend(["", "## 失败复盘", f"- {summary or '运行失败，未记录额外复盘摘要。'}"])
        if facts.adjustments:
            lines.extend(["", "## 计划与调整", *[f"- 调整：{item}" for item in facts.adjustments]])
        lines.extend(["", "## 限制与未验证事项", *[f"- {item}" for item in data["limitations"]]])
        return redact("\n".join(lines))

    @staticmethod
    def _reproduction_markdown(item: dict[str, Any]) -> str:
        expected = item.get("expected") or "无额外预期结果"
        if item.get("kind") == "http" and isinstance(item.get("http"), dict):
            http = item["http"]
            details = [f"{http.get('method', 'GET')} `{http.get('url', '')}`"]
            query = http.get("query")
            if isinstance(query, list) and query:
                details.append("查询参数 " + "、".join(f"`{key}` = `{value}`" for key, value in query if isinstance(key, str)))
            header = http.get("ctf_header")
            if isinstance(header, dict) and header.get("name") and header.get("value"):
                details.append(f"CTF 请求头 `{header['name']}`，值 `{header['value']}`")
            return f"{item['sequence']}. {'；'.join(details)}。预期：{expected}"
        if item.get("kind") == "decode" and isinstance(item.get("decode"), dict):
            decode = item["decode"]
            details = [
                f"使用 `{decode.get('encoding', 'auto')}` 解码",
                f"来源：{decode.get('source')}",
            ]
            if decode.get("field"):
                details.append(f"字段：`{decode['field']}`")
            if decode.get("json_pointer"):
                details.append(f"JSON Pointer：`{decode['json_pointer']}`")
            if decode.get("input") is not None:
                details.append(f"输入：`{decode['input']}`")
            return f"{item['sequence']}. {'；'.join(details)}。预期：{expected}"
        return f"{item['sequence']}. {item['action']}。预期：{expected}"
