from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel

from yuwang.domain.models import EvidenceCandidate, Observation, TaskSpec


class VerificationResult(BaseModel):
    verified: bool
    summary: str
    evidence_call_id: str | None = None
    rule_kind: str | None = None


class SuccessVerifier:
    """Deterministic verifier. Model output is only a candidate, never authority."""

    def verify(
        self,
        task: TaskSpec,
        candidate: EvidenceCandidate | None,
        observations: list[Observation],
    ) -> VerificationResult:
        if candidate is None:
            return VerificationResult(verified=False, summary="模型未提供带来源的候选答案")
        if not task.verification_rules:
            return VerificationResult(verified=False, summary="任务未配置确定性成功验证规则")
        observation = next(
            (
                item
                for item in observations
                if item.call_id == candidate.source_call_id and item.success
            ),
            None,
        )
        if observation is None:
            return VerificationResult(verified=False, summary="候选答案未关联成功工具调用")
        try:
            source_value = self._resolve_pointer(observation.output, candidate.location)
        except (KeyError, IndexError, TypeError, ValueError):
            return VerificationResult(verified=False, summary="候选证据位置无效")
        if str(source_value) != candidate.value:
            return VerificationResult(verified=False, summary="候选值与来源证据不一致")

        for rule in task.verification_rules:
            if rule.kind == "regex" and re.fullmatch(rule.value, candidate.value):
                return VerificationResult(
                    verified=True,
                    summary="候选答案通过正则验证",
                    evidence_call_id=str(candidate.source_call_id),
                    rule_kind=rule.kind,
                )
            if rule.kind == "sha256":
                digest = hashlib.sha256(candidate.value.encode("utf-8")).hexdigest()
                if digest == rule.value.lower():
                    return VerificationResult(
                        verified=True,
                        summary="候选答案通过 SHA-256 验证",
                        evidence_call_id=str(candidate.source_call_id),
                        rule_kind=rule.kind,
                    )
        return VerificationResult(verified=False, summary="候选答案未通过确定性规则")

    @staticmethod
    def _resolve_pointer(document: object, pointer: str) -> object:
        current = document
        for raw_part in pointer.lstrip("/").split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                current = current[part]
            elif isinstance(current, list):
                current = current[int(part)]
            else:
                raise TypeError("pointer traverses scalar")
        return current
