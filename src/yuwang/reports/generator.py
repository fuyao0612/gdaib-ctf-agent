"""从不可变审计数据生成 Markdown 与 JSON 双格式运行报告。"""

from __future__ import annotations

from typing import Any

from yuwang.domain.models import Event, Run, TaskSpec
from yuwang.policy import redact


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


class ReportGenerator:
    """把运行、事件和计量快照渲染成可下载的 Markdown/JSON 报告。"""

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
        validation_status = str(metrics.get("validation_status", run.validation_status))
        evidence_level = str(metrics.get("evidence_level", run.evidence_level))
        data = {
            "schema_version": "1.0",
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
                f"- 执行状态：`{data['execution_status']}`",
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
