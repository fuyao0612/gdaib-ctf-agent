"""Flag 候选格式检查；明确区分格式命中与赛题平台外部验证。"""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yuwang.tooling.contracts import ToolCallRequest, ToolSpec

from .base import CtfArtifactTool, ctf_spec


class FlagCandidateVerifyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    candidate: str = Field(min_length=1, max_length=512)
    flag_prefix: str = Field(default="flag", min_length=1, max_length=40)

    @field_validator("flag_prefix")
    @classmethod
    def restrict_prefix(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("Flag 前缀只能包含字母、数字、下划线或连字符")
        return value


class FlagCandidateVerifyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: str
    source_artifact_id: UUID
    execution_status: str = "completed"
    validation_status: str
    evidence_level: str = "artifact"
    message: str
    artifact_ids: list[UUID] = Field(default_factory=list)


class FlagCandidateVerifyTool(CtfArtifactTool[FlagCandidateVerifyInput, FlagCandidateVerifyOutput]):
    input_model = FlagCandidateVerifyInput
    output_model = FlagCandidateVerifyOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="flag_candidate_verify",
            display_name="检查 Flag 候选格式",
            description="验证候选 Flag 的题目格式与 Artifact 来源，不会访问或声称已通过赛题平台提交",
            capabilities=["flag", "validation", "evidence"],
            scenarios=["ctf"],
            permissions=["artifact:read"],
            timeout_seconds=5,
            error_codes=["artifact_not_found", "invalid_flag_format"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(
        self, value: FlagCandidateVerifyInput, request: ToolCallRequest | None
    ) -> FlagCandidateVerifyOutput:
        artifact, _ = self.artifacts.read(value.artifact_id, request, max_bytes=8 * 1024 * 1024)
        pattern = rf"{re.escape(value.flag_prefix)}\{{[^{{}}\r\n]{{1,200}}\}}"
        matched = bool(re.fullmatch(pattern, value.candidate))
        return FlagCandidateVerifyOutput(
            candidate=value.candidate,
            source_artifact_id=artifact.id,
            validation_status="format_matched" if matched else "format_not_matched",
            message=(
                "候选 Flag，尚未经过赛题平台验证"
                if matched
                else "候选值不符合指定 Flag 格式，尚未经过赛题平台验证"
            ),
        )
