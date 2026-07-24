"""有限候选的古典密码分析，不进行无限密钥爆破。"""

from __future__ import annotations

import string
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from yuwang.tooling.contracts import ToolCallRequest, ToolSpec

from .base import CtfArtifactTool, ctf_spec

CipherMethod = Literal["caesar", "atbash", "rot13"]
ENGLISH_FREQUENCY = {
    "a": 8.17, "b": 1.49, "c": 2.78, "d": 4.25, "e": 12.70, "f": 2.23,
    "g": 2.02, "h": 6.09, "i": 6.97, "j": 0.15, "k": 0.77, "l": 4.03,
    "m": 2.41, "n": 6.75, "o": 7.51, "p": 1.93, "q": 0.10, "r": 5.99,
    "s": 6.33, "t": 9.06, "u": 2.76, "v": 0.98, "w": 2.36, "x": 0.15,
    "y": 1.97, "z": 0.07,
}


def default_methods() -> list[CipherMethod]:
    return ["caesar", "atbash", "rot13"]


class ClassicalCipherAnalyzeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    methods: list[CipherMethod] = Field(default_factory=default_methods, max_length=3)
    max_candidates: int = Field(default=5, ge=1, le=10)


class CipherCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: CipherMethod
    key: str = Field(max_length=40)
    preview: str = Field(max_length=2_000)
    score: float


class ClassicalCipherAnalyzeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[CipherCandidate] = Field(default_factory=list, max_length=10)
    artifact_ids: list[UUID] = Field(default_factory=list)


class ClassicalCipherAnalyzeTool(CtfArtifactTool[ClassicalCipherAnalyzeInput, ClassicalCipherAnalyzeOutput]):
    input_model = ClassicalCipherAnalyzeInput
    output_model = ClassicalCipherAnalyzeOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="classical_cipher_analyze",
            display_name="古典密码分析",
            description="对上传 Artifact 的文本执行有限 Caesar、Atbash 与 ROT13 候选分析，并返回可解释评分",
            capabilities=["cipher", "statistics", "analysis"],
            scenarios=["ctf", "crypto"],
            permissions=["artifact:read"],
            timeout_seconds=15,
            error_codes=["artifact_not_found", "text_too_large"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(
        self, value: ClassicalCipherAnalyzeInput, request: ToolCallRequest | None
    ) -> ClassicalCipherAnalyzeOutput:
        _, content = self.artifacts.read(value.artifact_id, request, max_bytes=256 * 1024)
        text = content.decode("utf-8", errors="replace")
        candidates: list[CipherCandidate] = []
        methods = list(dict.fromkeys(value.methods))
        if "caesar" in methods:
            candidates.extend(
                CipherCandidate(
                    method="caesar", key=f"shift={shift}", preview=_caesar(text, shift)[:2_000], score=round(_score(_caesar(text, shift)), 3)
                )
                for shift in range(1, 26)
            )
        if "atbash" in methods:
            decoded = _atbash(text)
            candidates.append(CipherCandidate(method="atbash", key="alphabet mirror", preview=decoded[:2_000], score=round(_score(decoded), 3)))
        if "rot13" in methods:
            decoded = _caesar(text, 13)
            candidates.append(CipherCandidate(method="rot13", key="shift=13", preview=decoded[:2_000], score=round(_score(decoded), 3)))
        candidates.sort(key=lambda item: item.score, reverse=True)
        return ClassicalCipherAnalyzeOutput(candidates=candidates[:value.max_candidates])


def _caesar(value: str, shift: int) -> str:
    result: list[str] = []
    for char in value:
        if "a" <= char <= "z":
            result.append(chr((ord(char) - ord("a") - shift) % 26 + ord("a")))
        elif "A" <= char <= "Z":
            result.append(chr((ord(char) - ord("A") - shift) % 26 + ord("A")))
        else:
            result.append(char)
    return "".join(result)


def _atbash(value: str) -> str:
    return "".join(
        chr(ord("z") - (ord(char) - ord("a"))) if "a" <= char <= "z" else
        chr(ord("Z") - (ord(char) - ord("A"))) if "A" <= char <= "Z" else char
        for char in value
    )


def _score(value: str) -> float:
    letters = [char.lower() for char in value if char.lower() in string.ascii_lowercase]
    if len(letters) < 5:
        return -10_000.0
    total = len(letters)
    counts = {letter: letters.count(letter) for letter in string.ascii_lowercase}
    chi_square = sum(
        ((counts[letter] - total * expected / 100) ** 2) / max(total * expected / 100, 0.01)
        for letter, expected in ENGLISH_FREQUENCY.items()
    )
    common_words = sum(value.lower().count(word) for word in (" the ", " and ", " flag", "ctf"))
    return -chi_square + common_words * 20
