"""运行轨迹的唯一聚合入口。

这里仅消费 SQLite 中已经持久化的记录，因此报告、审计 API 与导出不会在收尾阶段
调用工具或补写结果。公开字段统一经过脱敏，长输出只保留安全预览和 Artifact 引用。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from yuwang.agent.repository import AgentRepository
from yuwang.domain.models import Run
from yuwang.policy import redact, redact_data


def _source(values: list[bool]) -> str:
    if not values:
        return "unavailable"
    if all(values):
        return "provider"
    if any(values):
        return "mixed"
    return "estimated"


class RunTraceService:
    """从持久化事实构建统一快照，避免 API、报告与页面各自重算。"""

    def __init__(self, repository: AgentRepository) -> None:
        self.repository = repository

    def metrics(self, run: Run) -> dict[str, Any]:
        model_calls = self.repository.list_model_calls(run.id)
        tool_calls = self.repository.list_tool_calls(run.id)
        steps = self.repository.list_execution_steps(run.id)
        input_tokens = sum(value.input_tokens for value in model_calls)
        output_tokens = sum(value.output_tokens for value in model_calls)
        provider_requests = sum(
            max(1, int(value.metadata.get("request_count", 1)))
            if isinstance(value.metadata, dict)
            else 1
            for value in model_calls
        )
        costs = [
            value.metadata.get("cost")
            for value in model_calls
            if isinstance(value.metadata, dict) and isinstance(value.metadata.get("cost"), (int, float))
        ]
        duration_ms = sum(value.duration_ms for value in model_calls) + sum(
            value.duration_ms for value in tool_calls
        )
        if run.started_at and run.finished_at:
            duration_ms = max(duration_ms, int((run.finished_at - run.started_at).total_seconds() * 1000))
        return {
            "logical_model_calls": len(model_calls),
            "provider_requests": provider_requests,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "model_cost": sum(float(value) for value in costs if isinstance(value, (int, float))),
            "cost_source": "provider" if costs else "unavailable",
            "token_source": _source([
                bool(value.metadata.get("usage_reported")) if isinstance(value.metadata, dict) else False
                for value in model_calls
            ]),
            "tool_calls": len(tool_calls),
            "tool_failures": sum(value.status == "failed" for value in tool_calls),
            "duration_ms": duration_ms,
            "steps": len(steps),
            "replans": sum(event.type == "replanned" for event in self.repository.list_events(run.id)),
            "manual_interventions": sum(
                event.type
                in {
                    "input_received", "clarification_received", "plan_edited", "plan_approved",
                    "plan_rejected", "risk_approved", "risk_rejected", "guidance_queued",
                    "pause_requested", "run_resumed",
                }
                for event in self.repository.list_events(run.id)
            ),
        }

    def snapshot(self, run_id: UUID | str) -> dict[str, Any]:
        run = self.repository.get_run(run_id)
        if not run:
            raise KeyError("run not found")
        task = self.repository.get_run_task(run.id)
        profile = self.repository.get_run_agent_profile(run.id)
        evidence = self.repository.list_evidence(run.id)
        artifacts = self.repository.list_run_artifacts(run.id)
        data = {
            "schema_version": "2.0",
            "exported_at": datetime.now(UTC).isoformat(),
            "run": run.model_dump(mode="json"),
            "task": task.model_dump(mode="json") if task else None,
            "provider": {"name": run.provider, "model": run.model},
            "plan": (
                [value.model_dump(mode="json") for value in self.repository.list_plan_revisions(run.id)]
                if task else []
            ),
            "execution_mode": (
                "动态执行（未预生成固定计划）"
                if profile and profile.planning_strategy == "direct"
                else "计划执行"
            ),
            "steps": [value.model_dump(mode="json") for value in self.repository.list_execution_steps(run.id)],
            "evidence": [value.model_dump(mode="json") for value in evidence],
            "artifacts": [
                {
                    "id": str(value.id), "filename": value.filename, "kind": value.kind,
                    "sha256": value.sha256, "size": value.size, "mime_type": value.mime_type,
                    "download_url": f"/api/v1/artifacts/{value.id}/download",
                }
                for value in artifacts
            ],
            "final_result": {
                "execution_status": run.status,
                "validation_status": run.validation_status,
                "evidence_level": run.evidence_level,
                "error": run.error,
            },
            "metrics": self.metrics(run),
        }
        sanitized = redact_data(data)
        assert isinstance(sanitized, dict)
        return sanitized

    @staticmethod
    def preview(output: dict[str, Any], fallback: str | None = None) -> str:
        """小而稳定的输出预览，完整内容继续保存在既有 Artifact。"""

        text = redact(str(output if output else fallback or ""))
        if len(text) <= 1200:
            return text
        return f"{text[:700]}\n...（已截断，详见关联 Artifact）...\n{text[-300:]}"
