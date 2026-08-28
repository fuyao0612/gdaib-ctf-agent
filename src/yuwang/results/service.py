"""把 Agent 候选结果转换为可审计、可持久化的 TaskResult。

这里刻意不接受模型声明的 validation_status、validator 或 tool_verified 字段。
这些字段只由服务端根据当前 Run 中已经存在的事实决定。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from yuwang.agent.repository import AgentRepository
from yuwang.domain.models import (
    Artifact,
    EvidenceRecord,
    EvidenceReference,
    ExecutionStep,
    Run,
    TaskResult,
    TaskSpec,
    ToolCall,
    ValidationStatus,
)


class TaskResultDraft(BaseModel):
    """Agent 可以输出的候选字段；不包含任何服务端验证结论。"""

    model_config = ConfigDict(extra="forbid")

    result_type: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=10_000)
    structured_data: dict[str, Any] = Field(default_factory=dict)
    evidence_candidates: list[Any] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=0, ge=0, le=1)


class EvidenceBinder:
    """只把当前 Run 可解析的真实 ID 转为公开证据引用。"""

    def __init__(self, repository: AgentRepository) -> None:
        self.repository = repository

    def bind(
        self,
        run: Run,
        task: TaskSpec,
        candidates: Iterable[Any],
    ) -> list[EvidenceReference]:
        evidence = {item.id: item for item in self.repository.list_evidence(run.id)}
        tools = {item.id: item for item in self.repository.list_tool_calls(run.id)}
        steps = {item.sequence: item for item in self.repository.list_execution_steps(run.id)}
        artifacts = {
            item.id: item
            for item in [
                *self.repository.list_run_artifacts(run.id),
                *(self.repository.get_artifact(value) for value in task.artifact_ids),
            ]
            if item is not None and (item.run_id in {None, run.id} or item.id in task.artifact_ids)
        }
        bound: list[EvidenceReference] = []
        seen: set[str] = set()
        for candidate in candidates:
            item = self._one(candidate, run, evidence, tools, steps, artifacts)
            if item is None or item.raw_ref in seen:
                continue
            seen.add(item.raw_ref)
            bound.append(item)
        return bound

    @staticmethod
    def _one(
        value: Any,
        run: Run,
        evidence: dict[UUID, EvidenceRecord],
        tools: dict[UUID, ToolCall],
        steps: dict[int, ExecutionStep],
        artifacts: dict[UUID, Artifact],
    ) -> EvidenceReference | None:
        raw_id: Any
        if isinstance(value, str):
            raw_id = value
            kind = "evidence"
        elif isinstance(value, dict):
            raw_id = value.get("id") or value.get("evidence_id") or value.get("raw_ref")
            kind = str(value.get("evidence_type") or value.get("type") or "evidence")
        else:
            return None
        try:
            identifier = UUID(str(raw_id))
        except (TypeError, ValueError):
            return None
        if identifier in evidence:
            evidence_item = evidence[identifier]
            return EvidenceReference(
                evidence_type=kind if kind != "evidence" else "evidence_record",
                source=str(evidence_item.source_call_id),
                content_summary=evidence_item.verification_summary,
                raw_ref=f"evidence:{evidence_item.id}",
                source_step=evidence_item.source_step,
                reliable=evidence_item.verified,
                tool_verified=evidence_item.verified,
            )
        if identifier in tools:
            tool_item = tools[identifier]
            return EvidenceReference(
                evidence_type=kind if kind != "evidence" else "tool_call",
                source=str(tool_item.id),
                content_summary=tool_item.result_summary,
                raw_ref=f"tool_call:{tool_item.id}",
                reliable=str(tool_item.status) == "succeeded",
                tool_verified=str(tool_item.status) == "succeeded",
            )
        if identifier in artifacts:
            artifact_item = artifacts[identifier]
            return EvidenceReference(
                evidence_type=kind if kind != "evidence" else "artifact",
                source=str(artifact_item.id),
                content_summary=f"{artifact_item.filename} ({artifact_item.sha256})",
                raw_ref=f"artifact:{artifact_item.id}",
                sha256=artifact_item.sha256,
                reliable=artifact_item.trust_level == "tool_verified",
                tool_verified=artifact_item.trust_level == "tool_verified",
            )
        return None


class TaskResultBuilder:
    """将候选和服务端事实组合成一个结果。"""

    def __init__(self, repository: AgentRepository) -> None:
        self.repository = repository
        self.binder = EvidenceBinder(repository)

    def build(
        self,
        run: Run,
        task: TaskSpec,
        draft: TaskResultDraft,
        *,
        fallback_evidence: Iterable[Any] = (),
    ) -> TaskResult:
        references = self.binder.bind(
            run,
            task,
            [*draft.evidence_candidates, *fallback_evidence],
        )
        status, validator, version, explanation = self._validation(run, draft, references)
        return TaskResult(
            result_type=draft.result_type,
            title=draft.title,
            summary=draft.summary,
            structured_data=draft.structured_data,
            scenario=str(task.scenario),
            evidence=references,
            validation_status=status,
            validator_name=validator,
            validator_version=version,
            validated_at=datetime.now(UTC) if status in {"validated", "failed"} else None,
            validation_explanation=explanation,
            confidence=draft.confidence,
            source_steps=[item.source_step for item in references if item.source_step],
            tool_call_ids=[UUID(item.source) for item in references if self._is_uuid(item.source)],
        )

    @staticmethod
    def _is_uuid(value: str) -> bool:
        try:
            UUID(value)
            return True
        except ValueError:
            return False

    @staticmethod
    def _validation(
        run: Run, draft: TaskResultDraft, references: list[EvidenceReference]
    ) -> tuple[ValidationStatus, str, str, str]:
        if str(run.status) in {"failed", "stopped"}:
            return "failed", "run-status", "1.0", "Run 未成功完成"
        if not references:
            return "unverified", "none", "0", "结果候选未绑定当前 Run 的真实证据"
        if str(draft.result_type) == "flag":
            if any(item.tool_verified and item.reliable for item in references):
                return "validated", "evidence-registry", "1.0", "结果已绑定并通过独立证据校验"
            return "partial", "evidence-registry", "1.0", "结果已绑定证据，但尚无确定性验证通过记录"
        if all(item.reliable for item in references):
            return "validated", "evidence-registry", "1.0", "结果引用的证据均来自已验证事实"
        return "partial", "evidence-registry", "1.0", "结果已绑定部分证据，仍需进一步验证"


class TaskResultService:
    """从 Agent 结构化输出生成并持久化一个或多个结果。"""

    def __init__(self, repository: AgentRepository) -> None:
        self.repository = repository
        self.builder = TaskResultBuilder(repository)

    def persist(
        self,
        run: Run,
        task: TaskSpec,
        structured_output: dict[str, Any] | None = None,
        *,
        final_answer: str | None = None,
        validation_status: ValidationStatus | None = None,
    ) -> list[TaskResult]:
        records = self.repository.list_evidence(run.id)
        drafts = self._drafts(structured_output, final_answer, task, records)
        evidence_ids = [str(item.id) for item in records]
        results = [
            self.builder.build(run, task, draft, fallback_evidence=evidence_ids) for draft in drafts
        ]
        if results:
            existing = {item.id for item in run.results}
            run.results.extend(item for item in results if item.id not in existing)
            run.validation_status = validation_status or self._run_status(run.results)
            self.repository.save_run(run)
        return results

    @staticmethod
    def _drafts(
        output: dict[str, Any] | None,
        final_answer: str | None,
        task: TaskSpec,
        evidence: list[EvidenceRecord],
    ) -> list[TaskResultDraft]:
        value = output or {}
        raw_results = value.get("results")
        if not isinstance(raw_results, list):
            raw_results = [value] if value.get("result_type") else []
        drafts: list[TaskResultDraft] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            try:
                drafts.append(TaskResultDraft.model_validate(item))
            except Exception:
                continue
        if drafts:
            return drafts
        if str(task.scenario) == "ctf" and evidence:
            return [
                TaskResultDraft(
                    result_type="flag",
                    title="CTF Flag 结果候选",
                    summary=item.verification_summary,
                    structured_data={"candidate": item.candidate},
                    evidence_candidates=[str(item.id)],
                    confidence=1.0 if item.verified else 0.25,
                )
                for item in evidence
            ]
        if final_answer:
            return [
                TaskResultDraft(
                    result_type="assessment" if str(task.scenario) != "ctf" else "flag",
                    title="Agent 结果候选",
                    summary=final_answer[:10_000],
                )
            ]
        return []

    @staticmethod
    def _run_status(results: list[TaskResult]) -> ValidationStatus:
        statuses = {item.validation_status for item in results}
        if statuses == {"validated"}:
            return "validated"
        if "failed" in statuses:
            return "failed"
        if "partial" in statuses:
            return "partial"
        return "unverified"
