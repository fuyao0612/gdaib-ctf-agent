"""从持久化事实生成 Markdown 和 JSON 报告。"""

from __future__ import annotations

from typing import Any

from yuwang.domain.models import Event, Run, TaskSpec
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


class ReportGenerator:
    """唯一报告路径：ReportFacts.build -> _data -> _markdown。"""

    def generate(
        self, run: Run, task: TaskSpec, events: list[Event], metrics: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        facts = ReportFacts.build(run, task, events, metrics)
        data = self._data(run, facts, metrics)
        return self._markdown(facts, data), data

    @staticmethod
    def _data(run: Run, facts: ReportFacts, metrics: dict[str, Any]) -> dict[str, Any]:
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
        data = {
            "schema_version": "2.1",
            "report_kind": facts.report_kind,
            "report_kind_reason": facts.report_kind_reason,
            "run_id": str(run.id),
            "task_summary": facts.task_summary,
            "mode": str(getattr(run, "mode", "agent")),
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
        }
        safe = redact_data(data)
        assert isinstance(safe, dict)
        return safe

    @staticmethod
    def _markdown(facts: ReportFacts, data: dict[str, Any]) -> str:
        title = "# 御网智元 CTF 解题报告" if facts.report_kind == "ctf" else "# 御网智元运行报告"
        lines = [title, "", "## 任务与结论"]
        lines.extend([
            f"- 执行状态：{facts.execution_status}",
            f"- 验证状态：{facts.validation_label}",
            f"- 任务：{facts.task_summary}",
        ])
        if facts.report_kind == "ctf":
            lines.extend(["", "## Flag 候选与验证状态"])
            lines.extend([
                f"- 候选 Flag：`{item['candidate']}`；格式校验：{item['format_status']}；"
                f"确定性验证：{item['deterministic_validation_status']}；"
                f"赛题平台验证：{item['platform_validation_status']}；来源：{item['discovery_source']}"
                for item in facts.candidates
            ] or ["- 未发现 Flag 候选。"])
            lines.extend(["", "## 解题思路摘要"])
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
            return f"{item['sequence']}. 使用 `{decode.get('encoding', 'auto')}` 解码；来源：{decode.get('source')}。预期：{expected}"
        return f"{item['sequence']}. {item['action']}。预期：{expected}"
