"""从不可变审计数据生成 Markdown 与 JSON 双格式运行报告。"""

from __future__ import annotations

from typing import Any, cast

from yuwang.domain.models import Event, Run, TaskSpec
from yuwang.policy import redact, redact_data


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
