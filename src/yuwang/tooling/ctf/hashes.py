"""哈希识别与完整性校验工具；只读取当前 Run 已授权的 Artifact。"""

from __future__ import annotations

import hashlib
import re
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yuwang.tooling.contracts import ToolCallRequest, ToolSpec

from .base import CtfArtifactTool, ctf_spec

MAX_HASH_READ_BYTES = 8 * 1024 * 1024
_HEX = re.compile(r"(?<![A-Fa-f0-9])([A-Fa-f0-9]{32}|[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64}|[A-Fa-f0-9]{96}|[A-Fa-f0-9]{128})(?![A-Fa-f0-9])")


class HashAnalyzeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    expected_digest: str | None = Field(default=None, max_length=128)
    max_embedded_hashes: int = Field(default=100, ge=1, le=200)

    @field_validator("expected_digest")
    @classmethod
    def normalize_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip().casefold()
        if not re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64}|[0-9a-f]{96}|[0-9a-f]{128}", candidate):
            raise ValueError("期望摘要必须是 32/40/64/96/128 位十六进制字符串")
        return candidate


class EmbeddedHash(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["md5", "sha1", "sha256", "sha384", "sha512"]
    digest: str = Field(min_length=32, max_length=128)
    line_number: int = Field(ge=1)


class FileDigests(BaseModel):
    model_config = ConfigDict(extra="forbid")

    md5: str
    sha1: str
    sha256: str
    sha384: str
    sha512: str


class HashAnalyzeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    file_digests: FileDigests
    embedded_hashes: list[EmbeddedHash] = Field(default_factory=list, max_length=200)
    expected_digest: str | None = None
    expected_matches: list[str] = Field(default_factory=list, max_length=5)
    input_truncated: bool = False


class HashAnalyzeTool(CtfArtifactTool[HashAnalyzeInput, HashAnalyzeOutput]):
    input_model = HashAnalyzeInput
    output_model = HashAnalyzeOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="hash_analyze",
            display_name="哈希识别与完整性校验",
            description=(
                "计算 Artifact 的常用摘要，识别文本中明确出现的哈希候选并可与期望摘要比对；"
                "不进行密码爆破、不调用外部服务"
            ),
            capabilities=["crypto", "hash", "integrity", "forensics"],
            scenarios=["ctf", "forensics", "incident_response", "reverse"],
            permissions=["artifact:read"],
            timeout_seconds=15,
            error_codes=["artifact_not_found", "file_too_large", "invalid_digest", "result_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(
        self, value: HashAnalyzeInput, request: ToolCallRequest | None
    ) -> HashAnalyzeOutput:
        artifact, content = self.artifacts.read(
            value.artifact_id, request, max_bytes=MAX_HASH_READ_BYTES
        )
        digests = FileDigests(
            md5=hashlib.md5(content, usedforsecurity=False).hexdigest(),
            sha1=hashlib.sha1(content, usedforsecurity=False).hexdigest(),
            sha256=hashlib.sha256(content).hexdigest(),
            sha384=hashlib.sha384(content).hexdigest(),
            sha512=hashlib.sha512(content).hexdigest(),
        )
        embedded: list[EmbeddedHash] = []
        text = content.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in _HEX.finditer(line):
                digest = match.group(1).casefold()
                embedded.append(
                    EmbeddedHash(
                        algorithm=_algorithm_for_length(len(digest)),
                        digest=digest,
                        line_number=line_number,
                    )
                )
                if len(embedded) >= value.max_embedded_hashes:
                    break
            if len(embedded) >= value.max_embedded_hashes:
                break
        expected_matches = (
            [algorithm for algorithm, digest in digests.model_dump().items() if digest == value.expected_digest]
            if value.expected_digest
            else []
        )
        return HashAnalyzeOutput(
            artifact_id=artifact.id,
            file_digests=digests,
            embedded_hashes=embedded,
            expected_digest=value.expected_digest,
            expected_matches=expected_matches,
            input_truncated=len(content) >= MAX_HASH_READ_BYTES,
        )


def _algorithm_for_length(length: int) -> Literal["md5", "sha1", "sha256", "sha384", "sha512"]:
    return cast(
        Literal["md5", "sha1", "sha256", "sha384", "sha512"],
        {32: "md5", 40: "sha1", 64: "sha256", 96: "sha384", 128: "sha512"}[length],
    )
