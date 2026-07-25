"""受限层数的常见编码解码工具，不执行代码也不递归扩张输入。"""

from __future__ import annotations

import base64
import binascii
import html
import re
from typing import Literal
from urllib.parse import unquote
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from yuwang.tooling.contracts import ToolCallRequest, ToolSpec

from .base import CtfArtifactTool, ctf_spec

EncodingType = Literal["auto", "base64", "base32", "hex", "url", "html"]
MAX_INLINE_DECODED_CHARS = 2_000


class EncodingDecodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    encoding: EncodingType = "auto"
    max_layers: int = Field(default=2, ge=1, le=3)


class DecodedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(max_length=MAX_INLINE_DECODED_CHARS)
    preview: str = Field(max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    decode_chain: list[EncodingType] = Field(default_factory=list, max_length=3)


class EncodingDecodeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[DecodedCandidate] = Field(default_factory=list, max_length=6)
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=6)
    input_truncated: bool = False


class EncodingDecodeTool(CtfArtifactTool[EncodingDecodeInput, EncodingDecodeOutput]):
    input_model = EncodingDecodeInput
    output_model = EncodingDecodeOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="encoding_decode",
            display_name="常见编码解码",
            description="对上传题目 Artifact 进行受限层数的 Base64、Base32、Hex、URL 或 HTML 实体解码",
            capabilities=["encoding", "decode"],
            scenarios=["ctf", "crypto", "forensics"],
            permissions=["artifact:read", "artifact:create"],
            timeout_seconds=10,
            error_codes=["artifact_not_found", "invalid_encoding", "decode_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
            artifact_types=["decoded_text"],
        )

    async def execute_with_request(
        self, value: EncodingDecodeInput, request: ToolCallRequest | None
    ) -> EncodingDecodeOutput:
        artifact, content = self.artifacts.read(value.artifact_id, request, max_bytes=256 * 1024)
        text = content.decode("utf-8", errors="replace")
        results: list[DecodedCandidate] = []
        artifact_ids: list[UUID] = []
        queue: list[tuple[str, list[EncodingType], float]] = [(text, [], 1.0)]
        seen = {text}
        for _ in range(value.max_layers):
            next_queue: list[tuple[str, list[EncodingType], float]] = []
            for current, chain, confidence in queue:
                kinds = [value.encoding] if value.encoding != "auto" else self._detect(current)
                for kind in kinds:
                    decoded = self._decode(current, kind)
                    if decoded is None or decoded in seen:
                        continue
                    seen.add(decoded)
                    next_chain = [*chain, kind]
                    next_confidence = confidence * (0.96 if value.encoding != "auto" else 0.78)
                    results.append(
                        DecodedCandidate(
                            value=decoded[:MAX_INLINE_DECODED_CHARS],
                            preview=decoded[:2_000],
                            confidence=round(next_confidence, 2),
                            decode_chain=next_chain,
                        )
                    )
                    if len(decoded) > MAX_INLINE_DECODED_CHARS:
                        created = self.artifacts.create(
                            artifact,
                            filename=f"decoded-{kind}.txt",
                            content=decoded.encode("utf-8"),
                            kind="decoded_text",
                            mime_type="text/plain",
                            run_id=request.run_id if request else None,
                        )
                        artifact_ids.append(created.id)
                    next_queue.append((decoded, next_chain, next_confidence))
                    if len(results) >= 6:
                        return EncodingDecodeOutput(
                            candidates=results,
                            artifact_ids=artifact_ids,
                            input_truncated=len(content) == 256 * 1024,
                        )
            queue = next_queue
            if not queue:
                break
        return EncodingDecodeOutput(
            candidates=results,
            artifact_ids=artifact_ids,
            input_truncated=len(content) == 256 * 1024,
        )

    @staticmethod
    def _detect(value: str) -> list[EncodingType]:
        stripped = value.strip()
        choices: list[EncodingType] = []
        if re.fullmatch(r"[A-Fa-f0-9\s]+", stripped) and len(re.sub(r"\s", "", stripped)) % 2 == 0:
            choices.append("hex")
        if re.fullmatch(r"[A-Za-z0-9+/=\s]+", stripped) and len(stripped.replace(" ", "")) >= 8:
            choices.append("base64")
        if re.fullmatch(r"[A-Z2-7=\s]+", stripped.upper()) and len(stripped) >= 8:
            choices.append("base32")
        if "%" in value:
            choices.append("url")
        if "&" in value and ";" in value:
            choices.append("html")
        return choices

    @staticmethod
    def _decode(value: str, kind: EncodingType) -> str | None:
        compact = re.sub(r"\s", "", value)
        try:
            if kind == "base64":
                padding = "=" * (-len(compact) % 4)
                return base64.b64decode(compact + padding, validate=True).decode("utf-8", errors="replace")
            if kind == "base32":
                padding = "=" * (-len(compact) % 8)
                return base64.b32decode(compact.upper() + padding, casefold=True).decode("utf-8", errors="replace")
            if kind == "hex":
                return bytes.fromhex(compact).decode("utf-8", errors="replace")
            if kind == "url":
                result = unquote(value)
                return result if result != value else None
            if kind == "html":
                result = html.unescape(value)
                return result if result != value else None
        except (ValueError, binascii.Error, UnicodeError):
            return None
        return None
