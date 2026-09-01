"""受控 Artifact 的通用安全分析工具，不执行样本也不接受宿主机路径。"""

# 解析器中的短分支保持紧凑，避免稀释安全规则主流程。
# ruff: noqa: E701

from __future__ import annotations

import hashlib
import ipaddress
import re
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import UUID

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator

from yuwang.tooling.contracts import ToolCallRequest, ToolSpec

from .base import CtfArtifactTool, ctf_spec

MAX_READ_BYTES = 2 * 1024 * 1024
_IPV4 = re.compile(
    r"(?<![\w.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\w.])"
)
_IPV6 = re.compile(r"(?<![\w:])[0-9A-Fa-f:]{2,45}(?![\w:])")
_SHA256 = re.compile(r"(?<![A-Fa-f0-9])[A-Fa-f0-9]{64}(?![A-Fa-f0-9])")
_DOMAIN = re.compile(
    r"(?<![\w.-])(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+(?:com|net|org|io|cn|dev|test|local)(?![\w.-])",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://[^\s\"'<>]{1,512}", re.IGNORECASE)
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}(?![\w.-])")
_CVE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
_FILE_PATH = re.compile(r"(?<!\w)(?:[A-Za-z]:\\[^\s\"'<>|?*]+|/(?:[^\s\"'<>]+/?)+)")
_STRING = re.compile(rb"[\x20-\x7e]{4,}")


class _ArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID


class IOCExtractInput(_ArtifactInput):
    max_indicators: int = Field(default=100, ge=1, le=500)


class IOCItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ipv4", "ipv6", "domain", "url", "sha256", "email", "cve", "file_path"]
    value: str = Field(min_length=1, max_length=512)
    occurrences: int = Field(ge=1)
    line_numbers: list[int] = Field(default_factory=list, min_length=1, max_length=100)
    confidence: float = Field(ge=0, le=1)


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
            description="从当前 Thread 已授权的文本 Artifact 提取并规范化 IP、域名、URL、哈希、邮箱、CVE 与路径，并保留来源行",
            capabilities=["forensics", "ioc", "artifact_analysis"],
            scenarios=["forensics", "incident_response", "ctf"],
            permissions=["artifact:read"],
            timeout_seconds=8,
            error_codes=["artifact_not_found", "file_too_large", "decode_error", "result_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(
        self, value: IOCExtractInput, request: ToolCallRequest | None
    ) -> IOCExtractOutput:
        artifact, content = self.artifacts.read(
            value.artifact_id, request, max_bytes=MAX_READ_BYTES
        )
        text = content.decode("utf-8", errors="replace")
        findings = _extract_iocs(text)
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

    async def execute_with_request(
        self, value: ArtifactSearchInput, request: ToolCallRequest | None
    ) -> ArtifactSearchOutput:
        artifact, content = self.artifacts.read(
            value.artifact_id, request, max_bytes=MAX_READ_BYTES
        )
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
                matches.append(
                    SearchMatch(
                        line=line_no, excerpt=_safe_excerpt(line, value.query, value.case_sensitive)
                    )
                )
        return ArtifactSearchOutput(
            artifact_id=artifact.id,
            query=value.query,
            match_count=count,
            matches=matches,
            input_truncated=len(content) >= MAX_READ_BYTES,
        )


class InterfaceDocInput(_ArtifactInput):
    max_endpoints: int = Field(default=100, ge=1, le=300)


class InterfaceEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = Field(min_length=1, max_length=12)
    path: str = Field(min_length=1, max_length=2048)
    parameters: list[str] = Field(default_factory=list, max_length=100)
    response_fields: list[str] = Field(default_factory=list, max_length=100)
    same_origin: bool = True
    manual_review: list[str] = Field(default_factory=list, max_length=20)


class InterfaceDocOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    format: Literal["openapi", "postman", "simple_json", "curl_text", "unknown"]
    endpoint_count: int = Field(ge=0)
    endpoints: list[InterfaceEndpoint] = Field(default_factory=list, max_length=300)
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=1)
    input_truncated: bool = False


class InterfaceDocAnalyzeTool(CtfArtifactTool[InterfaceDocInput, InterfaceDocOutput]):
    input_model = InterfaceDocInput
    output_model = InterfaceDocOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="interface_doc_analyze",
            display_name="接口文档静态分析",
            description="解析已授权 Artifact 中的 OpenAPI、Postman、JSON 接口描述或 curl 示例，不访问网络、不执行内容",
            capabilities=["api_document", "interface_analysis", "artifact_analysis"],
            scenarios=["general", "ctf", "vulnerability_analysis", "forensics"],
            permissions=["artifact:read"],
            timeout_seconds=10,
            error_codes=["artifact_not_found", "file_too_large", "parse_error", "result_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
            consumes=["text", "json", "yaml", "api_document"],
            produces=["interface_analysis"],
        )

    async def execute_with_request(
        self, value: InterfaceDocInput, request: ToolCallRequest | None
    ) -> InterfaceDocOutput:
        artifact, content = self.artifacts.read(
            value.artifact_id, request, max_bytes=MAX_READ_BYTES
        )
        text = content.decode("utf-8", errors="replace")
        endpoints, fmt = _parse_interface_document(text)
        truncated = len(endpoints) > value.max_endpoints
        endpoints = endpoints[: value.max_endpoints]
        result = InterfaceDocOutput(
            artifact_id=artifact.id,
            format=fmt,
            endpoint_count=len(endpoints),
            endpoints=endpoints,
            input_truncated=truncated or len(content) >= MAX_READ_BYTES,
        )
        if len(endpoints) > 40 or len(text) > 20_000:
            derived = self.artifacts.create_for_run(
                request,
                filename=f"{artifact.filename}.interfaces.json",
                content=result.model_dump_json().encode(),
                kind="interface_analysis",
                mime_type="application/json",
            )
            result.artifact_ids = [derived.id]
        return result


class WebEvidenceInput(_ArtifactInput):
    max_links: int = Field(default=50, ge=1, le=200)


class WebEvidenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    status_code: int | None = Field(default=None, ge=100, le=599)
    content_type: str = Field(default="", max_length=200)
    title: str | None = Field(default=None, max_length=300)
    form_fields: list[str] = Field(default_factory=list, max_length=100)
    parameter_names: list[str] = Field(default_factory=list, max_length=100)
    same_origin_links: list[str] = Field(default_factory=list, max_length=200)
    script_references: list[str] = Field(default_factory=list, max_length=100)
    suspicious_features: list[str] = Field(default_factory=list, max_length=30)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    redacted_summary: str = Field(default="", max_length=2000)


class _EvidenceHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.forms: list[str] = []
        self.links: list[str] = []
        self.scripts: list[str] = []
        self.params: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.casefold(): value for key, value in attrs}
        if tag.casefold() == "title":
            self._in_title = True
        if tag.casefold() == "form":
            self.forms.extend(v for k, v in attrs if k.casefold() in {"name", "id"} and v)
        if tag.casefold() in {"input", "textarea", "select", "button"}:
            self.params.extend(v for k, v in attrs if k.casefold() in {"name", "id"} and v)
        if tag.casefold() == "a" and attrs_map.get("href"):
            self.links.append(attrs_map["href"] or "")
        if tag.casefold() == "script" and attrs_map.get("src"):
            self.scripts.append(attrs_map["src"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


class WebEvidenceAnalyzeTool(CtfArtifactTool[WebEvidenceInput, WebEvidenceOutput]):
    input_model = WebEvidenceInput
    output_model = WebEvidenceOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="web_evidence_analyze",
            display_name="HTTP 响应证据分析",
            description="分析已有 HTTP evidence Artifact 的页面结构与可疑特征，不跟随链接、不执行脚本或提交表单",
            capabilities=["http_analysis", "web", "artifact_analysis"],
            scenarios=["general", "ctf", "vulnerability_analysis", "forensics"],
            permissions=["artifact:read"],
            timeout_seconds=8,
            error_codes=["artifact_not_found", "file_too_large", "parse_error", "sensitive_data"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
            consumes=["http_evidence", "http_evidence_truncated"],
            produces=["web_evidence_analysis"],
        )

    async def execute_with_request(
        self, value: WebEvidenceInput, request: ToolCallRequest | None
    ) -> WebEvidenceOutput:
        artifact, content = self.artifacts.read(
            value.artifact_id, request, max_bytes=MAX_READ_BYTES
        )
        text = content.decode("utf-8", errors="replace")
        parser = _EvidenceHTMLParser()
        try:
            parser.feed(text)
        except Exception:
            pass
        base = str(artifact.extracted_metadata.get("url", ""))
        links = []
        for raw in parser.links:
            resolved = urljoin(base, raw) if base else raw
            if base and urlsplit(resolved).netloc != urlsplit(base).netloc:
                continue
            if resolved and resolved not in links:
                links.append(resolved)
        suspicious = []
        lowered = text.casefold()
        for marker, label in (
            ("document.cookie", "页面包含 Cookie 访问迹象"),
            ("authorization", "页面包含 Authorization 字样"),
            ("password", "页面包含密码字段"),
            ("eval(", "页面包含动态求值迹象"),
        ):
            if marker in lowered:
                suspicious.append(label)
        summary = _redact_line(
            f"title={parser.title.strip()[:200]}; forms={len(parser.forms)}; links={len(links)}; scripts={len(parser.scripts)}"
        )
        return WebEvidenceOutput(
            artifact_id=artifact.id,
            content_type=artifact.mime_type,
            title=parser.title.strip()[:300] or None,
            form_fields=sorted(set(parser.forms))[:100],
            parameter_names=sorted(set(parser.params))[:100],
            same_origin_links=links[: value.max_links],
            script_references=parser.scripts[:100],
            suspicious_features=suspicious,
            evidence_refs=[f"artifact:{artifact.id}"],
            redacted_summary=summary,
        )


class SourcePatternInput(_ArtifactInput):
    max_findings: int = Field(default=100, ge=1, le=300)


class SourceFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: Literal[
        "python-shell",
        "python-sql-format",
        "javascript-eval",
        "javascript-shell",
        "path-traversal",
        "ssrf-url-join",
        "unsafe-deserialization",
        "weak-random",
        "hardcoded-credential",
        "file-upload-path",
    ]
    severity: Literal["medium", "high"]
    line: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=400)
    rationale: str = Field(min_length=1, max_length=200)
    manual_review: bool = True
    confidence: float = Field(default=0.82, ge=0, le=1)


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
            description="对已授权源码 Artifact 做受限危险模式初筛，不宣称完整 SAST；仅输出定位、规则依据和人工复核提示",
            capabilities=["source", "vulnerability_analysis", "static_analysis"],
            scenarios=["vulnerability_analysis", "forensics", "ctf"],
            permissions=["artifact:read"],
            timeout_seconds=10,
            error_codes=["artifact_not_found", "file_too_large", "decode_error", "result_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(
        self, value: SourcePatternInput, request: ToolCallRequest | None
    ) -> SourcePatternOutput:
        artifact, content = self.artifacts.read(
            value.artifact_id, request, max_bytes=MAX_READ_BYTES
        )
        findings: list[SourceFinding] = []
        rules: tuple[tuple[str, str, str, str], ...] = (
            (
                "python-shell",
                "high",
                r"\b(?:os\.system|subprocess\.(?:run|Popen|call))\s*\(.*(?:shell\s*=\s*True|[+f]?[\"'])",
                "命令构造可能混入未验证输入",
            ),
            (
                "python-sql-format",
                "high",
                r"\b(?:execute|executemany)\s*\(\s*(?:f[\"']|[\"'][^\n]*?(?:%|\+))",
                "SQL 语句疑似使用字符串拼接或格式化",
            ),
            (
                "javascript-eval",
                "high",
                r"\b(?:eval|Function)\s*\(",
                "动态求值会扩大不可信输入影响面",
            ),
            (
                "javascript-shell",
                "high",
                r"\b(?:exec|spawn|execFile)\s*\(",
                "子进程调用需要核对参数来源和白名单",
            ),
            (
                "path-traversal",
                "high",
                r"(?:\.\./|os\.path\.join\s*\([^\n]*(?:request|input|param))",
                "路径可能由不可信输入拼接，需核对规范化与目录边界",
            ),
            (
                "ssrf-url-join",
                "high",
                r"\b(?:requests?\.(?:get|post)|httpx\.(?:get|post))\s*\([^\n]*(?:url|request|input)",
                "请求 URL 可能由外部输入构造，需核对目标白名单",
            ),
            (
                "unsafe-deserialization",
                "high",
                r"\b(?:pickle\.loads?|yaml\.load\s*\(|marshal\.loads?)",
                "反序列化可能执行不可信载荷，需确认安全 Loader 与输入来源",
            ),
            (
                "weak-random",
                "medium",
                r"\b(?:random\.(?:random|randint|choice)|Math\.random)\s*\(",
                "非密码学随机数不适合令牌或安全边界",
            ),
            (
                "hardcoded-credential",
                "high",
                r"(?i)\b(?:password|api[_-]?key|secret|token)\s*=\s*['\"](?!<redacted>)",
                "疑似硬编码凭据，需人工确认并轮换",
            ),
            (
                "file-upload-path",
                "high",
                r"\b(?:save|write|open)\s*\([^\n]*(?:filename|upload|file)",
                "上传文件路径需核对基名化、目录隔离和覆盖风险",
            ),
        )
        for line_no, line in enumerate(
            content.decode("utf-8", errors="replace").splitlines(), start=1
        ):
            for rule_id, severity, expression, rationale in rules:
                if re.search(expression, line):
                    findings.append(
                        SourceFinding(
                            rule_id=rule_id,
                            severity=severity,
                            line=line_no,
                            excerpt=_redact_line(line),
                            rationale=rationale,
                        )
                    )
                    if len(findings) >= value.max_findings:
                        return SourcePatternOutput(
                            artifact_id=artifact.id,
                            findings=findings,
                            analyzed_lines=line_no,
                            input_truncated=True,
                        )
        return SourcePatternOutput(
            artifact_id=artifact.id,
            findings=findings,
            analyzed_lines=len(content.decode("utf-8", errors="replace").splitlines()),
            input_truncated=len(content) >= MAX_READ_BYTES,
        )


class BinaryStaticInput(_ArtifactInput):
    max_strings: int = Field(default=80, ge=1, le=300)


class BinaryStaticOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    format: Literal["elf", "pe", "mach_o", "unknown"]
    architecture: str = Field(max_length=80)
    entry_point_offset: int | None = Field(default=None, ge=0)
    file_size: int = Field(ge=0)
    printable_strings: list[str] = Field(default_factory=list, max_length=300)
    string_offsets: list[int] = Field(default_factory=list, max_length=300)
    strings_truncated: bool = False
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
            error_codes=[
                "artifact_not_found",
                "file_too_large",
                "malformed_binary",
                "result_limit",
            ],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(
        self, value: BinaryStaticInput, request: ToolCallRequest | None
    ) -> BinaryStaticOutput:
        artifact, content = self.artifacts.read(
            value.artifact_id, request, max_bytes=MAX_READ_BYTES
        )
        binary_format, architecture, entry = _binary_metadata(content)
        all_strings = list(_STRING.finditer(content))
        selected = all_strings[: value.max_strings]
        strings = [item.group().decode("ascii", errors="replace")[:200] for item in selected]
        offsets = [item.start() for item in selected]
        return BinaryStaticOutput(
            artifact_id=artifact.id,
            sha256=hashlib.sha256(content).hexdigest(),
            format=binary_format,
            architecture=architecture,
            entry_point_offset=entry,
            file_size=artifact.size,
            printable_strings=strings,
            string_offsets=offsets,
            strings_truncated=len(all_strings) > value.max_strings,
            input_truncated=len(content) >= MAX_READ_BYTES,
        )


def _parse_interface_document(
    text: str,
) -> tuple[
    list[InterfaceEndpoint], Literal["openapi", "postman", "simple_json", "curl_text", "unknown"]
]:
    endpoints: list[InterfaceEndpoint] = []
    try:
        value = yaml.safe_load(text)
    except Exception:
        value = None
    if isinstance(value, dict):
        if isinstance(value.get("paths"), dict):
            for path, methods in value["paths"].items():
                if not isinstance(methods, dict):
                    continue
                for method, operation in methods.items():
                    if method.casefold() not in {
                        "get",
                        "post",
                        "put",
                        "patch",
                        "delete",
                        "head",
                        "options",
                        "trace",
                    }:
                        continue
                    operation = operation if isinstance(operation, dict) else {}
                    params = [
                        str(item.get("name"))
                        for item in operation.get("parameters", [])
                        if isinstance(item, dict) and item.get("name")
                    ]
                    endpoints.append(
                        InterfaceEndpoint(
                            method=method.upper(),
                            path=str(path),
                            parameters=params,
                            response_fields=_response_fields(operation.get("responses")),
                        )
                    )
            return endpoints, "openapi"
        if isinstance(value.get("item"), list):

            def walk(items: list[object]) -> None:
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    request = item.get("request")
                    if isinstance(request, dict):
                        method = str(request.get("method", "GET")).upper()
                        url = request.get("url")
                        path = url.get("raw") if isinstance(url, dict) else url
                        if path:
                            endpoints.append(
                                InterfaceEndpoint(
                                    method=method,
                                    path=str(path),
                                    parameters=[
                                        str(x.get("key"))
                                        for x in request.get("url", {}).get("query", [])
                                        if isinstance(x, dict) and x.get("key")
                                    ],
                                )
                            )
                    if isinstance(item.get("item"), list):
                        walk(item["item"])

            walk(value["item"])
            return endpoints, "postman"
        for key in ("endpoints", "interfaces", "routes"):
            entries = value.get(key)
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("path"):
                        endpoints.append(
                            InterfaceEndpoint(
                                method=str(entry.get("method", "GET")).upper(),
                                path=str(entry["path"]),
                                parameters=[
                                    str(x)
                                    for x in entry.get("parameters", [])
                                    if isinstance(x, (str, int))
                                ],
                                response_fields=[
                                    str(x)
                                    for x in entry.get("response_fields", [])
                                    if isinstance(x, (str, int))
                                ],
                            )
                        )
                return endpoints, "simple_json"
    for match in re.finditer(
        r"\bcurl\s+(?:-X\s+([A-Za-z]+)\s+)?(?:[^\n]*?\s)?(['\"]?https?://[^'\"\s]+)",
        text,
        re.IGNORECASE,
    ):
        endpoints.append(
            InterfaceEndpoint(
                method=(match.group(1) or "GET").upper(),
                path=match.group(2).strip("'\""),
                manual_review=["curl 示例中的目标需人工确认授权"],
            )
        )
    return endpoints, "curl_text" if endpoints else "unknown"


def _response_fields(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    fields: list[str] = []
    for response in value.values():
        if not isinstance(response, dict):
            continue
        content = response.get("content")
        if isinstance(content, dict):
            for media in content.values():
                schema = media.get("schema") if isinstance(media, dict) else None
                props = schema.get("properties") if isinstance(schema, dict) else None
                if isinstance(props, dict):
                    fields.extend(str(key) for key in props)
    return list(dict.fromkeys(fields))[:100]


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
        value = value.rstrip(".,;:)]").casefold()
        try:
            parsed = urlsplit(value)
            # 查询参数和 URL 用户信息可能携带 Token/密码；证据只保留可复查的公开路径。
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        except ValueError:
            return value.split("?", 1)[0].split("#", 1)[0]
    if kind == "sha256":
        return value.casefold()
    if kind == "email":
        return value.casefold()
    if kind == "cve":
        return value.upper()
    if kind == "file_path":
        return value.rstrip(".,;:)]")
    return value


def _extract_iocs(text: str) -> list[IOCItem]:
    """提取有行号的受限 IOC；URL 包含的域名不重复作为独立域名统计。"""

    matches: dict[tuple[str, str], list[int]] = {}
    url_spans: set[tuple[int, int]] = {match.span() for match in _URL.finditer(text)}
    email_spans: set[tuple[int, int]] = {match.span() for match in _EMAIL.finditer(text)}
    for kind, pattern in (
        ("url", _URL),
        ("ipv4", _IPV4),
        ("ipv6", _IPV6),
        ("domain", _DOMAIN),
        ("sha256", _SHA256),
        ("email", _EMAIL),
        ("cve", _CVE),
        ("file_path", _FILE_PATH),
    ):
        for found in pattern.finditer(text):
            raw = found.group()
            if kind == "url":
                url_spans.add(found.span())
            elif kind == "domain" and any(
                start <= found.start() and found.end() <= end
                for start, end in (*url_spans, *email_spans)
            ):
                continue
            elif kind == "file_path" and any(
                start <= found.start() and found.end() <= end for start, end in url_spans
            ):
                continue
            if kind == "ipv6":
                try:
                    parsed = ipaddress.ip_address(raw)
                except ValueError:
                    continue
                if parsed.version != 6:
                    continue
            value = _normalize_ioc(kind, raw)
            line = text.count("\n", 0, found.start()) + 1
            matches.setdefault((kind, value), []).append(line)
    return [
        IOCItem(
            kind=kind,
            value=value,
            occurrences=len(lines),
            line_numbers=list(dict.fromkeys(lines))[:100],
            confidence=_ioc_confidence(kind),
        )
        for (kind, value), lines in sorted(matches.items())
    ]


def _ioc_confidence(kind: str) -> float:
    return 0.99 if kind in {"sha256", "cve", "ipv4", "ipv6"} else 0.95


def _redact_line(value: str) -> str:
    def redact(match: re.Match[str]) -> str:
        return match.group(0).split("=", 1)[0].split(":", 1)[0] + "=<redacted>"

    value = re.sub(
        r"(?i)(?:api[_-]?key|authorization|token|password)\s*[:=]\s*[^\s,;]+",
        redact,
        value,
    )
    return value[:400] or "<empty line>"


def _binary_metadata(
    content: bytes,
) -> tuple[Literal["elf", "pe", "mach_o", "unknown"], str, int | None]:
    if content.startswith(b"\x7fELF") and len(content) >= 20:
        machine = int.from_bytes(content[18:20], "little")
        architecture = {3: "x86", 62: "x86_64", 40: "arm", 183: "aarch64"}.get(
            machine, f"elf-machine-{machine}"
        )
        entry_offset = 24 if content[4:5] == b"\x02" else 24
        width = 8 if content[4:5] == b"\x02" else 4
        entry = (
            int.from_bytes(content[entry_offset : entry_offset + width], "little")
            if len(content) >= entry_offset + width
            else None
        )
        return "elf", architecture, entry
    if content.startswith(b"MZ") and len(content) >= 0x40:
        pe_offset = int.from_bytes(content[0x3C:0x40], "little")
        if len(content) >= pe_offset + 28 and content[pe_offset : pe_offset + 4] == b"PE\0\0":
            machine = int.from_bytes(content[pe_offset + 4 : pe_offset + 6], "little")
            architecture = {0x14C: "x86", 0x8664: "x86_64", 0xAA64: "aarch64"}.get(
                machine, f"pe-machine-{machine}"
            )
            entry = int.from_bytes(content[pe_offset + 24 : pe_offset + 28], "little")
            return "pe", architecture, entry
    magic = content[:4]
    if magic in {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
    }:
        return "mach_o", "unknown", None
    return "unknown", "unknown", None
