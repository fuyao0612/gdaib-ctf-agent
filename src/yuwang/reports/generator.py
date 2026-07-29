"""从不可变审计数据生成 Markdown 与 JSON 双格式运行报告。"""

from __future__ import annotations

from typing import Any, cast

from yuwang.domain.models import Event, Run, TaskSpec
from yuwang.policy import redact, redact_data
from yuwang.reports.facts import ReportFacts


def _clean_decision(value: object) -> str | None:
    text = str(value or "").strip()
    while text.startswith("下一步："):
        text = text.removeprefix("下一步：").strip()
    while text.startswith("结束："):
        text = text.removeprefix("结束：").strip()
    return text or None


def trust_notice(validation_status: str) -> str:
    """把验证结论翻译为用户可见的可信度说明。"""

    return {
        "pending": "验证尚未完成，不能视为验证通过",
        "unverified": "结果未经外部验证",
        "partial": "已完成部分校验，尚未完成外部验证",
        "validated": "结果已通过确定性外部验证",
        "failed": "验证失败，结果不能视为已验证成功",
    }.get(validation_status, "验证状态未知，不能视为验证通过")


def completion_summary(validation_status: str) -> str:
    """运行完成仅说明执行结束；验证结论由 validation_status 单独决定。"""

    return {
        "pending": "执行已结束，但验证状态尚未确认",
        "unverified": "执行已结束，结果未经过外部验证",
        "partial": "执行已结束，已完成部分校验但未完成外部验证",
        "validated": "执行已结束，结果已通过确定性外部验证",
        "failed": "执行已结束，但验证失败",
    }.get(validation_status, "执行已结束，验证状态未知")


def _is_ctf_report(task: TaskSpec, trace: dict[str, Any], evidence_records: list[Any]) -> tuple[bool, str]:
    raw_steps = trace.get("steps")
    steps: list[Any] = raw_steps if isinstance(raw_steps, list) else []
    if str(task.scenario).casefold() == "ctf":
        return True, "任务场景明确为 CTF"
    if any(isinstance(step, dict) and str(step.get("tool_id", "")).startswith("ctf.") for step in steps):
        return True, "运行使用了 CTF 专用工具"
    if any(isinstance(item, dict) and item.get("rule_kind") == "flag_format" for item in evidence_records):
        return True, "存在 Flag 格式校验证据"
    return False, "未发现 CTF 场景、工具或 Flag 证据"


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


class ReportGenerator:
    """把运行、事件和计量快照渲染成可下载的 Markdown/JSON 报告。"""

    def generate(
        self, run: Run, task: TaskSpec, events: list[Event], metrics: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Render both formats from one ReportFacts object."""
        facts = ReportFacts.build(run, task, events, metrics)
        data = self._data(run, facts, metrics)
        return self._markdown(facts, data), data

    @staticmethod
    def _data(run: Run, facts: ReportFacts, metrics: dict[str, Any]) -> dict[str, Any]:
        model = facts.metrics
        # Historical Run records are immutable.  A report can still derive its display
        # evidence level from the persisted successful tool-backed candidate source.
        report_evidence_level = (
            "external"
            if any(item.get("source_call_id") for item in facts.candidates)
            else metrics.get("evidence_level", run.evidence_level)
        )
        data = {
            "schema_version": "2.1", "report_kind": facts.report_kind,
            "report_kind_reason": facts.report_kind_reason, "run_id": str(run.id),
            "task_summary": facts.task_summary, "execution_status": facts.execution_status,
            "status": facts.execution_status, "validation_status": facts.validation_status,
            "validation_label": facts.validation_label,
            # Keep the established public fields while schema 2.1 adds structured facts.
            "trust_notice": trust_notice(facts.validation_status),
            "evidence_level": report_evidence_level,
            "completion_mode": metrics.get("completion_mode", run.completion_mode),
            # Schema 2.1 retains these schema 2.0 keys as read-only compatibility views.
            "mode": str(getattr(run, "mode", "agent")),
            "result": completion_summary(facts.validation_status),
            "plan": metrics.get("plan", {}).get("steps", []) if isinstance(metrics.get("plan"), dict) else [],
            "execution_mode": metrics.get("trace", {}).get("execution_mode", "计划执行") if isinstance(metrics.get("trace"), dict) else "计划执行",
            "final_answer": facts.final_answer,
            "structured_output": metrics.get("structured_output"),
            "flag_candidates": facts.candidates, "timeline": facts.timeline, "steps": facts.timeline,
            "key_clues": facts.key_clues,
            "evidence": [item.get("summary") for item in facts.key_clues if item.get("summary")],
            "verification_evidence": metrics.get("evidence_records", []),
            "artifacts": facts.artifacts, "reproduction_steps": facts.reproduction_steps,
            "failed_attempts": facts.failed_attempts, "policy_summary": facts.policy_summary,
            "adjustments": facts.adjustments,
            "metrics": model,
            "duration_ms": model.get("duration_ms", metrics.get("duration_ms", 0)),
            "errors": [run.error] if run.error else [],
            "policy_checks": [item["summary"] for item in facts.policy_summary],
            "model_metrics": {
                "calls": model.get("logical_model_calls", metrics.get("model_calls", 0)),
                "logical_model_calls": model.get("logical_model_calls", metrics.get("model_calls", 0)),
                "provider_requests": model.get("provider_requests", metrics.get("model_calls", 0)),
                "input_tokens": model.get("input_tokens", 0), "output_tokens": model.get("output_tokens", 0),
                "tokens": model.get("total_tokens", metrics.get("tokens", 0)),
            },
            "tool_metrics": {"calls": model.get("tool_calls", metrics.get("tool_calls", 0)), "failures": model.get("tool_failures", metrics.get("tool_failures", 0))},
            "limitations": [trust_notice(facts.validation_status)],
            "failure_analysis": metrics.get("failure_analysis"),
        }
        safe = redact_data(data)
        assert isinstance(safe, dict)
        return safe

    @staticmethod
    def _markdown(facts: ReportFacts, data: dict[str, Any]) -> str:
        lines = ["# 御网智元 CTF 解题报告" if facts.report_kind == "ctf" else "# 御网智元运行报告", ""]
        if facts.report_kind == "ctf":
            lines += ["## 一、任务概览与最终结论", f"- 执行状态：{facts.execution_status}", f"- 验证状态：{facts.validation_label}", f"- 任务：{facts.task_summary}", "", "## 二、Flag 候选与验证状态"]
            if facts.candidates:
                for item in facts.candidates:
                    platform = "已通过" if item["platform_verified"] else "未进行"
                    lines.append(f"- 候选 Flag：`{item['candidate']}`；格式校验：{item['format_status']}；外部平台验证：{platform}；来源：{item['source_kind']}")
            else:
                lines.append("- 未发现 Flag 候选。")
            lines += ["", "## 三、解题思路摘要"]
            lines += [f"- {item['summary']}" for item in facts.key_clues[:5]] or ["- 尚无可公开的关键观察。"]
            lines += ["", "## 四、详细执行过程"]
        else:
            lines += ["## 一、任务与结论", f"- 执行状态：{facts.execution_status}", f"- 验证状态：{facts.validation_label}", f"- 任务：{facts.task_summary}", "", "## 二、执行过程"]
        for step in facts.timeline:
            decision = _clean_decision(step.get("decision")) or "运行在此步骤结束，未记录后续公开动作"
            lines += [f"### 步骤 {step.get('sequence')}", f"- 目标：{step.get('goal')}", f"- 行动：{step.get('action_summary')}", f"- 关键观察：{step.get('observation_summary') or '无'}", f"- 决策：{decision}", f"- 调用：`{step.get('call_id')}`"]
        if facts.report_kind == "ctf":
            lines += ["", "## 五、关键线索与证据"]
            lines += [f"- 步骤 {item['step']}：{item['summary']}" for item in facts.key_clues] or ["- 暂无经过验证的证据记录。"]
        lines += ["", "## 六、可复现步骤"]
        for item in facts.reproduction_steps:
            lines.append(ReportGenerator._reproduction_markdown(item))
        lines += ["", "## 七、失败尝试与调整"]
        lines += ([f"- 步骤 {item.get('sequence')}：{item.get('error') or item.get('observation_summary')}" for item in facts.failed_attempts] or ["- 本次运行未记录失败工具调用。"])
        if facts.execution_status == "failed":
            analysis = data.get("failure_analysis")
            summary = analysis.get("summary") if isinstance(analysis, dict) else None
            lines += ["", "## 失败复盘", f"- {summary or '运行失败，未记录额外复盘摘要。'}"]
        lines += ["", "## 八、Artifact 清单"]
        lines += [
            f"- {item.get('filename')}；{item.get('mime_type')}；{item.get('size')} B；"
            f"SHA-256 {str(item.get('sha256', ''))[:12]}；来源步骤：{item.get('source_step') or '未记录'}；"
            f"source_call_id：{item.get('source_call_id') or '未记录'}；下载：{item.get('download_url')}"
            for item in facts.artifacts
        ] or ["- 无关联 Artifact。"]
        lines += ["", "## 九、资源消耗与审计", f"- 逻辑模型调用：{data['model_metrics']['logical_model_calls']}，实际 Provider 请求：{data['model_metrics']['provider_requests']}；Token：{data['model_metrics']['tokens']}", f"- 工具调用：{data['tool_metrics']['calls']}；失败：{data['tool_metrics']['failures']}"]
        lines += [f"- 策略：{item['summary']}（{item['count']} 次）" for item in facts.policy_summary]
        lines += ["", "## 十、限制与未验证事项", *[f"- {value}" for value in data["limitations"]]]
        if facts.adjustments:
            lines += ["", "## 十一、计划与调整", *[f"- 调整：{value}" for value in facts.adjustments]]
        return redact("\n".join(lines))

    @staticmethod
    def _reproduction_markdown(item: dict[str, Any]) -> str:
        """Keep Markdown reproduction instructions readable instead of dumping JSON."""

        expected = item.get("expected") or "无额外预期结果"
        if item.get("kind") == "http" and isinstance(item.get("http"), dict):
            http = item["http"]
            details = [f"{http.get('method', 'GET')} `{http.get('url', '')}`"]
            query = http.get("query")
            if isinstance(query, list) and query:
                details.append("查询参数 " + "、".join(f"`{key}` = `{value}`" for key, value in query if isinstance(key, str)))
            header = http.get("ctf_header")
            if isinstance(header, dict):
                name, value = header.get("name"), header.get("value")
                if name and value:
                    details.append(f"CTF 请求头 `{name}`，值 `{value}`")
            return f"{item['sequence']}. {'；'.join(details)}。预期：{expected}"
        if item.get("kind") == "decode" and isinstance(item.get("decode"), dict):
            decode = item["decode"]
            details = [f"使用 `{decode.get('encoding', 'auto')}` 解码", f"来源：{decode.get('source', '已记录输入')}"]
            if decode.get("json_pointer"):
                details.append(f"提取字段 `{decode['json_pointer']}`")
            if decode.get("input"):
                details.append(f"输入：`{decode['input']}`")
            return f"{item['sequence']}. {'；'.join(details)}。预期：{expected}"
        return f"{item['sequence']}. {item['action']}。预期：{expected}"

    def _legacy_generate(
        self, run: Run, task: TaskSpec, events: list[Event], metrics: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        trace_value = metrics.get("trace")
        trace = cast(dict[str, Any], trace_value) if isinstance(trace_value, dict) else {}
        replans = [event.summary for event in events if str(event.type) == "replanned"]
        # 关键证据只能来自 EvidenceRecord；工具完成或 Artifact 创建本身不是证据。
        evidence: list[str] = []
        raw_evidence_records = metrics.get("evidence_records")
        evidence_records: list[Any] = raw_evidence_records if isinstance(raw_evidence_records, list) else []
        for record in evidence_records:
            if isinstance(record, dict):
                evidence.append(
                    f"候选证据 {record.get('source_call_id')} {record.get('location')}："
                    f"{record.get('verification_summary')}"
                )
        policy = _dedupe([event.summary for event in events if str(event.type) == "policy_checked"])
        plan_data = metrics.get("plan") or {}
        plan_steps = plan_data.get("steps", []) if isinstance(plan_data, dict) else []
        validation_status = str(metrics.get("validation_status", run.validation_status))
        evidence_level = str(metrics.get("evidence_level", run.evidence_level))
        raw_failure_analysis = metrics.get("failure_analysis")
        failure_analysis = (
            cast(dict[str, Any], raw_failure_analysis)
            if isinstance(raw_failure_analysis, dict)
            else None
        )
        failure_summary = (
            str(failure_analysis.get("summary", "")).strip() if failure_analysis else ""
        )
        raw_unified_metrics = trace.get("metrics")
        unified_metrics = (
            cast(dict[str, Any], raw_unified_metrics)
            if isinstance(raw_unified_metrics, dict)
            else {}
        )
        raw_trace_steps = trace.get("steps")
        trace_steps: list[Any] = raw_trace_steps if isinstance(raw_trace_steps, list) else []
        is_ctf, report_kind_reason = _is_ctf_report(task, trace, evidence_records)
        raw_artifacts = trace.get("artifacts")
        artifacts: list[Any] = raw_artifacts if isinstance(raw_artifacts, list) else []
        timeline = [step for step in trace_steps if isinstance(step, dict)]
        flag_candidates = [
            {
                "candidate": item.get("candidate"), "source_call_id": item.get("source_call_id"),
                "validation_status": "format_matched" if item.get("rule_kind") == "flag_format" else "unverified",
                "platform_verified": bool(item.get("verified", False)),
            }
            for item in evidence_records if isinstance(item, dict) and item.get("candidate")
        ]
        key_clues = _dedupe([
            str(step.get("observation_summary")) for step in timeline
            if step.get("observation_summary")
        ])
        reproduction_steps = [
            f"{index}. {step.get('action_summary', step.get('goal', '执行已记录动作'))}"
            for index, step in enumerate(timeline, 1)
        ]
        failed_attempts = [step for step in timeline if step.get("observation_status") in {"error", "timeout", "blocked", "stopped"}]
        data = {
            "schema_version": "2.0",
            "report_kind": "ctf" if is_ctf else "general",
            "report_kind_reason": report_kind_reason,
            "run_id": str(run.id),
            "task_summary": redact(task.body[:500]),
            "execution_status": str(run.status),
            # status 是早期报告字段；保留它以便旧客户端下载，同时用 execution_status
            # 明确说明这只描述生命周期，绝不代表验证通过。
            "mode": str(task.mode),
            "status": str(run.status),
            "completion_mode": metrics.get("completion_mode", run.completion_mode),
            "validation_status": validation_status,
            "evidence_level": evidence_level,
            "trust_notice": trust_notice(validation_status),
            "final_answer": metrics.get("final_answer"),
            "structured_output": metrics.get("structured_output"),
            "result": metrics.get("verification") or completion_summary(validation_status)
            if str(run.status) == "completed"
            else (failure_summary or run.error or "运行未完成"),
            "plan": plan_steps,
            "execution_mode": trace.get("execution_mode", "计划执行"),
            "steps": trace_steps,
            "timeline": timeline,
            "key_clues": key_clues,
            "verification_evidence": evidence_records,
            "artifacts": artifacts,
            "flag_candidates": flag_candidates,
            "reproduction_steps": reproduction_steps,
            "failed_attempts": failed_attempts,
            "adjustments": replans,
            "evidence": evidence,
            "tool_metrics": {
                "calls": unified_metrics.get("tool_calls", metrics.get("tool_calls", 0)),
                "failures": unified_metrics.get("tool_failures", metrics.get("tool_failures", 0)),
            },
            "model_metrics": {
                "calls": unified_metrics.get("logical_model_calls", metrics.get("model_calls", 0)),
                "logical_model_calls": unified_metrics.get("logical_model_calls", metrics.get("model_calls", 0)),
                "provider_requests": unified_metrics.get("provider_requests", metrics.get("model_calls", 0)),
                "input_tokens": unified_metrics.get("input_tokens", 0),
                "output_tokens": unified_metrics.get("output_tokens", 0),
                "tokens": unified_metrics.get("total_tokens", metrics.get("tokens", 0)),
            },
            "metrics": unified_metrics,
            "duration_ms": unified_metrics.get("duration_ms", metrics.get("duration_ms", 0)),
            "errors": [run.error] if run.error else [],
            "failure_analysis": failure_analysis,
            "policy_checks": policy,
            "policy_summary": [{"summary": item, "count": sum(event.summary == item for event in events if str(event.type) == "policy_checked")} for item in policy],
            "limitations": [trust_notice(validation_status)],
        }
        sanitized_data = redact_data(data)
        assert isinstance(sanitized_data, dict)
        data = sanitized_data
        artifact_lines = [
            f"- {item.get('filename')} · {item.get('kind')} · {item.get('size')} B · SHA-256 {str(item.get('sha256', ''))[:12]}"
            for item in artifacts
        ] or ["- 无"]
        lines = [
            "# 御网智元 CTF 解题报告" if is_ctf else "# 御网智元运行报告",
            "",
            "## 一、任务与结论" if is_ctf else "## 任务与结论",
            f"- 运行：`{run.id}`",
            f"- 状态：**{run.status}**；验证状态：`{data['validation_status']}`",
            f"- 报告类型：{'CTF' if is_ctf else '通用任务'}（{report_kind_reason}）",
            f"- 任务：{data['task_summary']}",
            "",
            "## 二、Flag 与验证状态" if is_ctf else "## 执行摘要",
        ]
        if is_ctf:
            lines.extend([f"- 候选 Flag：`{item['candidate']}`；格式校验：{item['validation_status']}；赛题平台验证：{'已通过' if item['platform_verified'] else '未执行'}" for item in flag_candidates] or ["- 未获得 Flag 候选。"])
        else:
            lines.append(str(data["result"]))
        lines.extend([
            "", "## 三、详细执行过程" if is_ctf else "## 执行过程",
            *[
                f"### 步骤 {step.get('sequence')}\n- 目标：{step.get('goal')}\n- 行动：{step.get('action_summary')}\n- 关键观察：{step.get('observation_summary') or '无'}\n- 下一步：{step.get('decision') or '未记录后续公开决策'}"
                for step in timeline
            ],
            *( ["", "## 四、关键线索与证据", *[f"- {item}" for item in key_clues]] if is_ctf else [] ),
            "", "## 可复现步骤", *reproduction_steps,
            "", "## Artifact 清单", *artifact_lines,
            "", "## 资源消耗与审计",
            f"- 逻辑模型调用：{data['model_metrics']['logical_model_calls']}，实际 Provider 请求：{data['model_metrics']['provider_requests']}，Token：{data['model_metrics']['tokens']}",
            f"- 工具调用：{data['tool_metrics']['calls']}，失败：{data['tool_metrics']['failures']}",
            *[f"- 策略：{item['summary']}（{item['count']} 次）" for item in data['policy_summary']],
            "", "## 限制与未验证事项", *[f"- {item}" for item in data['limitations']],
        ])
        if data.get("failure_analysis"):
            lines.extend([
                "", "## 失败复盘", str(data["failure_analysis"].get("summary", "")),
                *[f"- 建议：{item}" for item in data["failure_analysis"].get("next_steps", [])],
            ])
        if replans:
            lines.extend(["", "## 计划与调整", *[f"- 调整：{item}" for item in replans]])
        markdown = "\n".join(
            lines
        )
        return redact(markdown), data
