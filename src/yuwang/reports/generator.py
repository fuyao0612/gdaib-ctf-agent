"""从不可变审计数据生成 Markdown 与 JSON 双格式运行报告。"""

from __future__ import annotations

from typing import Any

from yuwang.domain.models import Event, Run, TaskSpec
from yuwang.policy import redact


class ReportGenerator:
    def generate(
        self, run: Run, task: TaskSpec, events: list[Event], metrics: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        replans = [event.summary for event in events if str(event.type) == "replanned"]
        evidence = [
            event.summary
            for event in events
            if str(event.type) in {"tool_finished", "artifact_created"}
        ]
        for record in metrics.get("evidence_records", []):
            if isinstance(record, dict):
                evidence.append(
                    f"候选证据 {record.get('source_call_id')} {record.get('location')}："
                    f"{record.get('verification_summary')}"
                )
        policy = [event.summary for event in events if str(event.type) == "policy_checked"]
        plan_data = metrics.get("plan") or {}
        plan_steps = plan_data.get("steps", []) if isinstance(plan_data, dict) else []
        data = {
            "schema_version": "1.0",
            "run_id": str(run.id),
            "task_summary": redact(task.body[:500]),
            "mode": str(task.mode),
            "status": str(run.status),
            "completion_mode": metrics.get("completion_mode", run.completion_mode),
            "validation_status": metrics.get("validation_status", run.validation_status),
            "evidence_level": metrics.get("evidence_level", run.evidence_level),
            "trust_notice": (
                "模型生成，未经外部验证"
                if metrics.get("validation_status") == "unverified"
                else "结果已按配置验证"
            ),
            "final_answer": metrics.get("final_answer"),
            "structured_output": metrics.get("structured_output"),
            "result": metrics.get("verification", "成功条件已验证")
            if str(run.status) == "completed"
            else (run.error or "运行未完成"),
            "plan": plan_steps,
            "adjustments": replans,
            "evidence": evidence,
            "tool_metrics": {
                "calls": metrics.get("tool_calls", 0),
                "failures": metrics.get("tool_failures", 0),
            },
            "model_metrics": {
                "calls": metrics.get("model_calls", 0),
                "tokens": metrics.get("tokens", 0),
            },
            "duration_ms": metrics.get("duration_ms", 0),
            "errors": [run.error] if run.error else [],
            "policy_checks": policy,
        }
        markdown = "\n".join(
            [
                "# 御网智元运行报告",
                "",
                f"- 运行：`{run.id}`",
                f"- 模式：`{task.mode}`",
                f"- 状态：**{run.status}**",
                f"- 完成模式：`{data['completion_mode']}`",
                f"- 验证状态：`{data['validation_status']}`",
                f"- 证据等级：`{data['evidence_level']}`",
                f"- 可信提示：**{data['trust_notice']}**",
                f"- 任务：{data['task_summary']}",
                "",
                "## 执行摘要",
                data["result"],
                *([str(data["final_answer"])] if data["final_answer"] else []),
                "",
                "## 计划与调整",
                *([f"- {item}" for item in data["plan"]] or ["- 未生成计划"]),
                *[f"- 调整：{item}" for item in replans],
                "",
                "## 关键证据",
                *([f"- {item}" for item in evidence] or ["- 无"]),
                "",
                "## 指标与审计",
                f"- 模型调用：{data['model_metrics']['calls']}，Token：{data['model_metrics']['tokens']}",
                f"- 工具调用：{data['tool_metrics']['calls']}，失败：{data['tool_metrics']['failures']}",
                *[f"- 策略：{item}" for item in policy],
            ]
        )
        return redact(markdown), data
