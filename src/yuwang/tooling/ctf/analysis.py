"""受控 Artifact 的通用安全分析工具，不执行样本也不接受宿主机路径。"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yuwang.tooling.contracts import ToolCallRequest, ToolSpec

from .base import CtfArtifactTool, ctf_spec

MAX_READ_BYTES = 2 * 1024 * 1024
_IPV4 = re.compile(r"(?<![\w.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\w.])")
_SHA256 = re.compile(r"(?<![A-Fa-f0-9])[A-Fa-f0-9]{64}(?![A-Fa-f0-9])")
_DOMAIN = re.compile(r"(?<![\w.-])(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+(?:com|net|org|io|cn|dev|test|local)(?![\w.-])", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s\"'<>]{1,512}", re.IGNORECASE)
_STRING = re.compile(rb"[\x20-\x7e]{4,}")


class _ArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID


class IOCExtractInput(_ArtifactInput):
    max_indicators: int = Field(default=100, ge=1, le=500)


class IOCItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ipv4", "domain", "url", "sha256"]
    value: str = Field(min_length=1, max_length=512)
    occurrences: int = Field(ge=1)


class IOCExtractOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    indicators: list[IOCItem] = Field(default_factory=list, max_length=500)
    input_truncated: bool = False


class IOCExtractTool(CtfArtifactTool[IOCExtractInput, IOCExtractOutput]):
    input_model = IOCExtractInput
    output_model = IOCExtractOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="ioc_extract",
            display_name="IOC 提取与规范化",
            description="从当前 Thread 已授权的文本 Artifact 提取并规范化 IP、域名、URL 与 SHA-256",
            capabilities=["forensics", "ioc", "artifact_analysis"],
            scenarios=["forensics", "incident_response", "ctf"],
            permissions=["artifact:read"],
            timeout_seconds=8,
            error_codes=["artifact_not_found", "file_too_large", "decode_error", "result_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(self, value: IOCExtractInput, request: ToolCallRequest | None) -> IOCExtractOutput:
        artifact, content = self.artifacts.read(value.artifact_id, request, max_bytes=MAX_READ_BYTES)
        text = content.decode("utf-8", errors="replace")
        findings: list[IOCItem] = []
        for kind, pattern in (
            ("ipv4", _IPV4),
            ("domain", _DOMAIN),
            ("url", _URL),
            ("sha256", _SHA256),
        ):
            counts = Counter(_normalize_ioc(kind, match.group()) for match in pattern.finditer(text))
            findings.extend(
                IOCItem(kind=kind, value=item, occurrences=count)
                for item, count in sorted(counts.items())
            )
        findings.sort(key=lambda item: (item.kind, item.value))
        return IOCExtractOutput(
            artifact_id=artifact.id,
            indicators=findings[: value.max_indicators],
            input_truncated=len(content) >= MAX_READ_BYTES,
        )


class ArtifactSearchInput(_ArtifactInput):
    query: str = Field(min_length=2, max_length=120)
    max_matches: int = Field(default=50, ge=1, le=200)
    case_sensitive: bool = False

    @field_validator("query")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("查询不能包含控制字符")
        return value


class SearchMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=500)


class ArtifactSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    query: str
    match_count: int = Field(ge=0)
    matches: list[SearchMatch] = Field(default_factory=list, max_length=200)
    input_truncated: bool = False


class ArtifactSearchTool(CtfArtifactTool[ArtifactSearchInput, ArtifactSearchOutput]):
    input_model = ArtifactSearchInput
    output_model = ArtifactSearchOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="artifact_content_search",
            display_name="Artifact 内容定位",
            description="在当前 Thread 已授权的文本 Artifact 中按字面量定位线索并返回受限上下文",
            capabilities=["forensics", "search", "artifact_analysis"],
            scenarios=["forensics", "incident_response", "reverse", "vulnerability_analysis"],
            permissions=["artifact:read"],
            timeout_seconds=8,
            error_codes=["artifact_not_found", "file_too_large", "invalid_query", "result_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(self, value: ArtifactSearchInput, request: ToolCallRequest | None) -> ArtifactSearchOutput:
        artifact, content = self.artifacts.read(value.artifact_id, request, max_bytes=MAX_READ_BYTES)
        lines = content.decode("utf-8", errors="replace").splitlines()
        needle = value.query if value.case_sensitive else value.query.casefold()
        matches: list[SearchMatch] = []
        count = 0
        for line_no, line in enumerate(lines, start=1):
            candidate = line if value.case_sensitive else line.casefold()
            if needle not in candidate:
                continue
            count += 1
            if len(matches) < value.max_matches:
                matches.append(SearchMatch(line=line_no, excerpt=_safe_excerpt(line, value.query, value.case_sensitive)))
        return ArtifactSearchOutput(
            artifact_id=artifact.id,
            query=value.query,
            match_count=count,
            matches=matches,
            input_truncated=len(content) >= MAX_READ_BYTES,
        )


class SourcePatternInput(_ArtifactInput):
    max_findings: int = Field(default=100, ge=1, le=300)


class SourceFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: Literal["python-shell", "python-sql-format", "javascript-eval", "javascript-shell"]
    severity: Literal["medium", "high"]
    line: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=400)
    rationale: str = Field(min_length=1, max_length=200)


class SourcePatternOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    findings: list[SourceFinding] = Field(default_factory=list, max_length=300)
    analyzed_lines: int = Field(ge=0)
    input_truncated: bool = False


class SourcePatternAnalyzeTool(CtfArtifactTool[SourcePatternInput, SourcePatternOutput]):
    input_model = SourcePatternInput
    output_model = SourcePatternOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="source_dangerous_pattern_analyze",
            display_name="结构化源码危险模式分析",
            description="对已授权源码 Artifact 检查受限的命令执行、动态求值和 SQL 拼接模式，仅输出定位与规则依据",
            capabilities=["source", "vulnerability_analysis", "static_analysis"],
            scenarios=["vulnerability_analysis", "forensics", "ctf"],
            permissions=["artifact:read"],
            timeout_seconds=10,
            error_codes=["artifact_not_found", "file_too_large", "decode_error", "result_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(self, value: SourcePatternInput, request: ToolCallRequest | None) -> SourcePatternOutput:
        artifact, content = self.artifacts.read(value.artifact_id, request, max_bytes=MAX_READ_BYTES)
        findings: list[SourceFinding] = []
        rules: tuple[tuple[str, str, str, str], ...] = (
            ("python-shell", "high", r"\b(?:os\.system|subprocess\.(?:run|Popen|call))\s*\(.*(?:shell\s*=\s*True|[+f]?[\"'])", "命令构造可能混入未验证输入"),
            ("python-sql-format", "high", r"\b(?:execute|executemany)\s*\(\s*(?:f[\"']|[\"'][^\n]*?(?:%|\+))", "SQL 语句疑似使用字符串拼接或格式化"),
            ("javascript-eval", "high", r"\b(?:eval|Function)\s*\(", "动态求值会扩大不可信输入影响面"),
            ("javascript-shell", "high", r"\b(?:exec|spawn|execFile)\s*\(", "子进程调用需要核对参数来源和白名单"),
        )
        for line_no, line in enumerate(content.decode("utf-8", errors="replace").splitlines(), start=1):
            for rule_id, severity, expression, rationale in rules:
                if re.search(expression, line):
                    findings.append(SourceFinding(rule_id=rule_id, severity=severity, line=line_no, excerpt=_redact_line(line), rationale=rationale))
                    if len(findings) >= value.max_findings:
                        return SourcePatternOutput(artifact_id=artifact.id, findings=findings, analyzed_lines=line_no, input_truncated=True)
        return SourcePatternOutput(artifact_id=artifact.id, findings=findings, analyzed_lines=len(content.decode("utf-8", errors="replace").splitlines()), input_truncated=len(content) >= MAX_READ_BYTES)


class BinaryStaticInput(_ArtifactInput):
    max_strings: int = Field(default=80, ge=1, le=300)


class BinaryStaticOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    format: Literal["elf", "pe", "mach_o", "unknown"]
    architecture: str = Field(max_length=80)
    entry_point_offset: int | None = Field(default=None, ge=0)
    printable_strings: list[str] = Field(default_factory=list, max_length=300)
    input_truncated: bool = False


class BinaryStaticAnalyzeTool(CtfArtifactTool[BinaryStaticInput, BinaryStaticOutput]):
    input_model = BinaryStaticInput
    output_model = BinaryStaticOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="binary_static_metadata_analyze",
            display_name="二进制静态元数据分析",
            description="读取已授权二进制 Artifact 的格式、架构、入口偏移和可打印字符串，不加载或执行样本",
            capabilities=["reverse", "binary", "static_analysis"],
            scenarios=["reverse", "reverse_static", "forensics", "ctf"],
            permissions=["artifact:read"],
            timeout_seconds=10,
            error_codes=["artifact_not_found", "file_too_large", "malformed_binary", "result_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(self, value: BinaryStaticInput, request: ToolCallRequest | None) -> BinaryStaticOutput:
        artifact, content = self.artifacts.read(value.artifact_id, request, max_bytes=MAX_READ_BYTES)
        binary_format, architecture, entry = _binary_metadata(content)
        strings = [item.decode("ascii", errors="replace")[:200] for item in _STRING.findall(content)[: value.max_strings]]
        return BinaryStaticOutput(artifact_id=artifact.id, sha256=hashlib.sha256(content).hexdigest(), format=binary_format, architecture=architecture, entry_point_offset=entry, printable_strings=strings, input_truncated=len(content) >= MAX_READ_BYTES)


def _safe_excerpt(line: str, query: str, case_sensitive: bool) -> str:
    source = line if case_sensitive else line.casefold()
    needle = query if case_sensitive else query.casefold()
    index = source.find(needle)
    start = max(0, index - 100)
    return _redact_line(line[start : index + len(query) + 180])


def _normalize_ioc(kind: str, value: str) -> str:
    if kind == "domain":
        return value.casefold().rstrip(".")
    if kind == "url":
        return value.rstrip(".,;:)]").casefold()
    if kind == "sha256":
        return value.casefold()
    return value


def _redact_line(value: str) -> str:
    def redact(match: re.Match[str]) -> str:
        return match.group(0).split("=", 1)[0].split(":", 1)[0] + "=<redacted>"

    value = re.sub(
        r"(?i)(?:api[_-]?key|authorization|token|password)\s*[:=]\s*[^\s,;]+",
        redact,
        value,
    )
    return value[:400] or "<empty line>"


def _binary_metadata(content: bytes) -> tuple[Literal["elf", "pe", "mach_o", "unknown"], str, int | None]:
    if content.startswith(b"\x7fELF") and len(content) >= 20:
        machine = int.from_bytes(content[18:20], "little")
        architecture = {3: "x86", 62: "x86_64", 40: "arm", 183: "aarch64"}.get(machine, f"elf-machine-{machine}")
        entry_offset = 24 if content[4:5] == b"\x02" else 24
        width = 8 if content[4:5] == b"\x02" else 4
        entry = int.from_bytes(content[entry_offset : entry_offset + width], "little") if len(content) >= entry_offset + width else None
        return "elf", architecture, entry
    if content.startswith(b"MZ") and len(content) >= 0x40:
        pe_offset = int.from_bytes(content[0x3C:0x40], "little")
        if len(content) >= pe_offset + 28 and content[pe_offset : pe_offset + 4] == b"PE\0\0":
            machine = int.from_bytes(content[pe_offset + 4 : pe_offset + 6], "little")
            architecture = {0x14C: "x86", 0x8664: "x86_64", 0xAA64: "aarch64"}.get(machine, f"pe-machine-{machine}")
            entry = int.from_bytes(content[pe_offset + 24 : pe_offset + 28], "little")
            return "pe", architecture, entry
    magic = content[:4]
    if magic in {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"}:
        return "mach_o", "unknown", None
    return "unknown", "unknown", None
