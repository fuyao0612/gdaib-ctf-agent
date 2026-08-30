"""仅在评测侧运行的确定性本地 Judge。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from yuwang.domain.models import Artifact, TaskResult

JudgeStatus = Literal["passed", "failed", "not_executed", "configuration_error"]


class JudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: JudgeStatus
    summary: str
    validator_name: str
    validator_version: str
    evidence_metadata: dict[str, Any] = Field(default_factory=dict)
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LocalJudge:
    """私有配置不进入 Agent Task、提示词或普通运行事件。"""

    version = "1.0"

    def __init__(self, judge_type: str) -> None:
        self.judge_type = judge_type

    def validate(
        self,
        candidate: TaskResult,
        private_config: dict[str, Any],
        *,
        artifact_lookup: Callable[[str], Artifact | None] | None = None,
    ) -> JudgeResult:
        if not candidate.evidence:
            return self._result("not_executed", "结果未绑定证据，Judge 不执行", {})
        if self.judge_type == "exact_hash":
            value = self._field(candidate, private_config.get("field"))
            expected = private_config.get("expected_sha256")
            if not isinstance(expected, str) or len(expected) != 64:
                return self._result("configuration_error", "exact_hash 缺少私有期望哈希", {})
            actual = hashlib.sha256(self._canonical(value).encode("utf-8")).hexdigest()
            return self._result(
                "passed" if actual == expected.lower() else "failed",
                "候选值哈希匹配" if actual == expected.lower() else "候选值哈希不匹配",
                {"field": private_config.get("field", ""), "actual_sha256": actual},
            )
        if self.judge_type == "structured_value":
            field = private_config.get("field")
            if not isinstance(field, str) or "expected_value" not in private_config:
                return self._result("configuration_error", "structured_value 缺少私有字段或期望值", {})
            actual = self._field(candidate, field)
            matched = actual == private_config["expected_value"]
            return self._result(
                "passed" if matched else "failed",
                "结构化字段匹配" if matched else "结构化字段不匹配",
                {"field": field},
            )
        if self.judge_type == "structured_fields":
            expected_fields = private_config.get("expected_fields")
            if not isinstance(expected_fields, dict) or not expected_fields:
                return self._result("configuration_error", "structured_fields 缺少私有期望字段", {})
            mismatches: dict[str, Any] = {}
            for field, expected in expected_fields.items():
                if not isinstance(field, str) or not field:
                    return self._result("configuration_error", "structured_fields 字段名无效", {})
                actual = self._field(candidate, field)
                if actual != expected:
                    mismatches[field] = {"expected": expected, "actual": actual}
            return self._result(
                "failed" if mismatches else "passed",
                "结构化字段全部匹配" if not mismatches else "结构化字段不匹配",
                {"fields": sorted(str(field) for field in expected_fields), "mismatches": mismatches},
            )
        if self.judge_type == "file_hash":
            expected = private_config.get("expected_sha256")
            artifact_id = private_config.get("artifact_id")
            if not isinstance(expected, str) or len(expected) != 64 or not isinstance(artifact_id, str):
                return self._result("configuration_error", "file_hash 缺少私有 Artifact 或期望哈希", {})
            artifact = artifact_lookup(artifact_id) if artifact_lookup else None
            if artifact is None:
                return self._result("not_executed", "私有配置引用的 Artifact 不可读取", {})
            matched = artifact.sha256 == expected.lower()
            return self._result(
                "passed" if matched else "failed",
                "Artifact 哈希匹配" if matched else "Artifact 哈希不匹配",
                {"artifact_id": artifact_id, "actual_sha256": artifact.sha256},
            )
        if self.judge_type == "platform_result":
            value = private_config.get("result")
            if value not in {"passed", "failed"}:
                return self._result("configuration_error", "platform_result 缺少已记录的平台结果", {})
            return self._result(value, "读取已记录的平台验证结果", {"platform_result": value})
        return self._result("configuration_error", f"不支持的 Judge 类型：{self.judge_type}", {})

    def _result(self, status: JudgeStatus, summary: str, metadata: dict[str, Any]) -> JudgeResult:
        return JudgeResult(
            status=status,
            summary=summary,
            validator_name=f"local_judge:{self.judge_type}",
            validator_version=self.version,
            evidence_metadata=metadata,
        )

    @staticmethod
    def _canonical(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _field(candidate: TaskResult, field: object) -> Any:
        value: Any = candidate.structured_data
        if not isinstance(field, str) or not field:
            return value
        for part in field.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return value


__all__ = ["JudgeResult", "JudgeStatus", "LocalJudge"]
