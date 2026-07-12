from __future__ import annotations

from typing import Any

from yuwang.domain.models import Event, Run, TaskSpec
from yuwang.policy import redact


class ReportGenerator:
    def generate(self, run: Run, task: TaskSpec, events: list[Event], metrics: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        replans = [event.summary for event in events if str(event.type) == "replanned"]
        evidence = [event.summary for event in events if str(event.type) in {"tool_finished", "artifact_created"}]
        policy = [event.summary for event in events if str(event.type) == "policy_checked"]
        data = {
            "schema_version": "1.0",
            "run_id": str(run.id),
            "task_summary": redact(task.body[:500]),
            "mode": str(task.mode),
            "status": str(run.status),
            "result": "成功条件已验证" if str(run.status) == "completed" else (run.error or "运行未完成"),
            "plan": ["规范化任务", "检查策略", "调用参考工具", "验证成功条件", "生成报告"],
            "adjustments": replans,
            "evidence": evidence,
            "tool_metrics": {"calls": metrics.get("tool_calls", 0), "failures": metrics.get("tool_failures", 0)},
            "model_metrics": {"calls": metrics.get("model_calls", 0), "tokens": metrics.get("tokens", 0)},
            "duration_ms": metrics.get("duration_ms", 0),
            "errors": [run.error] if run.error else [],
            "policy_checks": policy,
        }
        markdown = "\n".join([
            "# 御网智元运行报告",
            "",
            f"- 运行：`{run.id}`",
            f"- 模式：`{task.mode}`",
            f"- 状态：**{run.status}**",
            f"- 任务：{data['task_summary']}",
            "",
            "## 执行摘要",
            data["result"],
            "",
            "## 计划与调整",
            *[f"- {item}" for item in data["plan"]],
            *[f"- 调整：{item}" for item in replans],
            "",
            "## 关键证据",
            *([f"- {item}" for item in evidence] or ["- 无"]),
            "",
            "## 指标与审计",
            f"- 模型调用：{data['model_metrics']['calls']}，Token：{data['model_metrics']['tokens']}",
            f"- 工具调用：{data['tool_metrics']['calls']}，失败：{data['tool_metrics']['failures']}",
            *[f"- 策略：{item}" for item in policy],
        ])
        return redact(markdown), data
