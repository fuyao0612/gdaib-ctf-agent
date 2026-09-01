"""JWT/JWS 静态研判工具；只解析受控 Artifact，不验签、不伪造、不发起网络请求。"""

from __future__ import annotations

import base64
import json
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from yuwang.tooling.contracts import ToolCallRequest, ToolSpec

from .base import CtfArtifactTool, ctf_spec

MAX_READ_BYTES = 1 * 1024 * 1024
MAX_CANDIDATES = 50
_JWT = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,2048}\.[A-Za-z0-9_-]{2,8192}\.[A-Za-z0-9_-]{0,4096}(?![A-Za-z0-9_-])")
_SENSITIVE = {"token", "access_token", "refresh_token", "password", "passwd", "secret", "api_key", "apikey", "private_key"}


class JwtAnalyzeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    max_candidates: int = Field(default=20, ge=1, le=MAX_CANDIDATES)


class JwtCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    header: list[JwtField] = Field(default_factory=list, max_length=50)
    claims: list[JwtField] = Field(default_factory=list, max_length=100)
    signature_present: bool
    warnings: list[str] = Field(default_factory=list, max_length=20)
    source_offset: int = Field(ge=0)


class JwtField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=120)
    value: Any


class JwtAnalyzeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    candidate_count: int = Field(ge=0)
    candidates: list[JwtCandidate] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    input_truncated: bool = False


class JwtAnalyzeTool(CtfArtifactTool[JwtAnalyzeInput, JwtAnalyzeOutput]):
    input_model = JwtAnalyzeInput
    output_model = JwtAnalyzeOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="jwt_analyze",
            display_name="JWT 令牌静态研判",
            description="从已授权文本 Artifact 识别并解析 JWT/JWS 头部与声明，提示 alg=none、危险外部引用和缺失标准字段；不验签、不猜密钥、不修改令牌",
            capabilities=["web", "jwt", "token_analysis", "artifact_analysis"],
            scenarios=["ctf", "web", "vulnerability_analysis", "forensics"],
            permissions=["artifact:read"],
            timeout_seconds=8,
            error_codes=["artifact_not_found", "file_too_large", "decode_error", "result_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(
        self, value: JwtAnalyzeInput, request: ToolCallRequest | None
    ) -> JwtAnalyzeOutput:
        artifact, content = self.artifacts.read(value.artifact_id, request, max_bytes=MAX_READ_BYTES)
        text = content.decode("utf-8", errors="replace")
        candidates: list[JwtCandidate] = []
        seen: set[str] = set()
        for match in _JWT.finditer(text):
            token = match.group(0)
            if token in seen:
                continue
            seen.add(token)
            parsed = _parse_candidate(token, len(candidates) + 1, match.start())
            if parsed is not None:
                candidates.append(parsed)
                if len(candidates) >= value.max_candidates:
                    break
        return JwtAnalyzeOutput(
            artifact_id=artifact.id,
            candidate_count=len(candidates),
            candidates=candidates,
            input_truncated=len(content) >= MAX_READ_BYTES,
        )


def _decode_json(segment: str) -> dict[str, Any] | None:
    padding = "=" * (-len(segment) % 4)
    try:
        raw = base64.urlsafe_b64decode(segment + padding)
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _parse_candidate(token: str, index: int, offset: int) -> JwtCandidate | None:
    header_segment, claims_segment, signature = token.split(".", 2)
    header = _decode_json(header_segment)
    claims = _decode_json(claims_segment)
    if header is None or claims is None:
        return None
    safe_claims = [_field(key, value) for key, value in claims.items()]
    warnings: list[str] = []
    algorithm = str(header.get("alg", "")).casefold()
    if algorithm == "none":
        warnings.append("alg=none：令牌未声明签名算法，必须人工确认是否允许")
    if not signature:
        warnings.append("签名段为空：当前内容不能作为已验证令牌")
    for key in ("jku", "jwk", "x5u"):
        if key in header:
            warnings.append(f"头部包含外部密钥引用 {key}，需要人工审查来源")
    for key in ("exp", "iat", "iss", "sub"):
        if key not in claims:
            warnings.append(f"缺少常见声明 {key}")
    return JwtCandidate(
        index=index,
        header=[_field(key, value) for key, value in header.items()],
        claims=safe_claims,
        signature_present=bool(signature),
        warnings=warnings,
        source_offset=offset,
    )


def _redact(key: str, value: Any) -> Any:
    if key.casefold() in _SENSITIVE:
        return "[已脱敏]"
    if isinstance(value, str):
        return value[:1_000]
    if isinstance(value, list):
        return [_redact(key, item) for item in value[:20]]
    if isinstance(value, dict):
        return {str(item_key): _redact(str(item_key), item_value) for item_key, item_value in list(value.items())[:50]}
    return value


def _field(key: str, value: Any) -> JwtField:
    return JwtField(key=key[:120], value=_redact(key, value))
