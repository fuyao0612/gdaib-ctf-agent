"""平台内置的低风险参考工具。"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import ParseResult, urljoin, urlparse
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from yuwang.domain.models import Artifact

from .contracts import ToolCallRequest, ToolSpec
from .plugin import ToolPlugin
from .registry import ToolRegistry


class FileMetadataInput(BaseModel):
    path: str


class FileMetadataOutput(BaseModel):
    sha256: str
    size: int
    mime_type: str


class FileMetadataTool(ToolPlugin[FileMetadataInput, FileMetadataOutput]):
    input_model = FileMetadataInput
    output_model = FileMetadataOutput

    def __init__(self, artifact_root: Path) -> None:
        self.root = artifact_root.resolve()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="file_metadata",
            version="1.0.0",
            description="计算受控附件的哈希、大小与 MIME，不解析内容",
            capabilities=["file", "metadata"],
            scenarios=["general", "forensics"],
            risk="low",
            permissions=["artifact:read"],
            requires_network=False,
            allowed_target_types=["artifact"],
            timeout_seconds=5,
            error_codes=["path_denied", "not_found"],
            idempotent=True,
            artifact_types=[],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute(self, value: FileMetadataInput) -> FileMetadataOutput:
        candidate = (self.root / value.path).resolve()
        if self.root not in candidate.parents or not candidate.is_file():
            raise ValueError("路径不在受控 Artifact 目录中或文件不存在")
        data = await asyncio.to_thread(candidate.read_bytes)
        return FileMetadataOutput(
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            mime_type=mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
        )


MAX_PROBE_RESPONSE_BYTES = 32 * 1024
MAX_PROBE_EXCERPT_CHARS = 6_000


class ExplicitCtfHeader(BaseModel):
    """只允许来自已发现线索的 CTF 请求头，不接受通用认证头。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=7,
        max_length=64,
        pattern=r"^X-CTF-[A-Za-z0-9-]+$",
    )
    value: str = Field(min_length=1, max_length=256)

    @field_validator("value")
    @classmethod
    def reject_header_injection(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("CTF 请求头不能包含换行符")
        return value


class ProbeInput(BaseModel):
    """本机 CTF 取证输入；仅允许无副作用的 HTTP 方法。"""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2_048)
    method: Literal["GET", "HEAD", "OPTIONS"] = "GET"
    ctf_header: ExplicitCtfHeader | None = None


class ProbeHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=64)
    value: str = Field(max_length=512)


class ProbeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status_code: int
    method: Literal["GET", "HEAD", "OPTIONS"] = "GET"
    content_type: str = Field(max_length=200)
    body_excerpt: str = Field(default="", max_length=MAX_PROBE_EXCERPT_CHARS)
    body_truncated: bool = False
    response_headers: list[ProbeHeader] = Field(default_factory=list, max_length=4)
    explicit_links: list[str] = Field(default_factory=list, max_length=20)
    robots_paths: list[str] = Field(default_factory=list, max_length=20)
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=1)


class _ExplicitLinkParser(HTMLParser):
    """只采集页面已经写出的链接，调用方不会自动跟随它们。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attribute = "src" if tag.casefold() == "script" else "href"
        for name, value in attrs:
            if name.casefold() == attribute and value:
                self.links.append(value)
                return


class _HttpEvidenceRepository(Protocol):
    """本机 HTTP 取证只需要的最小持久化能力，避免工具依赖完整存储实现。"""

    def get_run(self, run_id: UUID | str) -> Any: ...

    def save_artifact(self, value: Artifact) -> Artifact: ...


class LocalhostHTTPProbeTool(ToolPlugin[ProbeInput, ProbeOutput]):
    input_model = ProbeInput
    output_model = ProbeOutput

    def __init__(
        self, artifact_root: Path | None = None, repository: _HttpEvidenceRepository | None = None
    ) -> None:
        self.artifact_root = artifact_root.resolve() if artifact_root else None
        self.repository = repository

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="localhost_http_probe",
            # 保持 1.1.0，兼容已持久化的 Run 工具快照；新增 method 为向后兼容字段。
            version="1.1.0",
            description=(
                "仅对任务明确授权的 localhost/127.0.0.1 执行只读 GET、HEAD 或 OPTIONS，"
                "返回受限正文摘要、响应头和页面已声明的 CTF 线索；不发送请求体、不跟随重定向"
            ),
            capabilities=["http", "metadata", "ctf_evidence"],
            scenarios=["general", "ctf", "web"],
            risk="low",
            permissions=["network:localhost"],
            requires_network=True,
            allowed_target_types=["localhost"],
            timeout_seconds=5,
            error_codes=["request_failed", "target_denied", "response_too_large"],
            idempotent=True,
            artifact_types=["http_evidence"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute(self, value: ProbeInput) -> ProbeOutput:
        """兼容旧 SDK 直接调用；正式 Run 会由 execute_with_request 再校验授权范围。"""

        return await self._probe(value, None)

    async def execute_with_request(
        self, value: ProbeInput, request: ToolCallRequest | None
    ) -> ProbeOutput:
        self._validate_target_scope(value.url, request.target_scope if request else [])
        return await self._probe(value, request)

    async def _probe(
        self, value: ProbeInput, request: ToolCallRequest | None
    ) -> ProbeOutput:
        parsed = self._validate_local_url(value.url)
        headers = {value.ctf_header.name: value.ctf_header.value} if value.ctf_header else {}
        request_url = self._loopback_request_url(value.url, parsed)
        timeout = httpx.Timeout(5, connect=2)
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=timeout, trust_env=False
        ) as client:
            async with client.stream(value.method, request_url, headers=headers) as response:
                content_type = response.headers.get("content-type", "")[:200]
                body, body_truncated = await self._read_limited_body(response)
                response_headers = [
                    ProbeHeader(name=name, value=response.headers[name][:512])
                    for name in ("content-type", "content-length", "location", "allow")
                    if name in response.headers
                ]
        if not self._is_textual(content_type):
            return ProbeOutput(
                status_code=response.status_code,
                method=value.method,
                content_type=content_type,
                body_excerpt="响应不是文本或 JSON，未读取为可执行内容。",
                body_truncated=body_truncated,
                response_headers=response_headers,
            )
        text = body.decode("utf-8", errors="replace")
        excerpt = text[:MAX_PROBE_EXCERPT_CHARS]
        artifact_ids = await self._save_response_artifact(
            request, body, content_type, body_truncated
        )
        return ProbeOutput(
            status_code=response.status_code,
            method=value.method,
            content_type=content_type,
            body_excerpt=excerpt,
            body_truncated=body_truncated or len(text) > MAX_PROBE_EXCERPT_CHARS,
            response_headers=response_headers,
            explicit_links=self._explicit_links(text, value.url, parsed),
            robots_paths=self._robots_paths(text, parsed.path),
            artifact_ids=artifact_ids,
        )

    async def _save_response_artifact(
        self,
        request: ToolCallRequest | None,
        body: bytes,
        content_type: str,
        body_truncated: bool,
    ) -> list[UUID]:
        """将文本响应作为当前 Run 的证据 Artifact，供解码与候选 Flag 校验复用。"""

        if (
            request is None
            or request.run_id is None
            or self.repository is None
            or self.artifact_root is None
            or not body
        ):
            return []
        run = self.repository.get_run(request.run_id)
        if not run:
            return []
        suffix = ".json" if content_type.casefold().startswith("application/json") else ".txt"
        storage_ref = f"{run.thread_id}/http-evidence-{request.call_id}{suffix}"
        destination = (self.artifact_root / storage_ref).resolve()
        if self.artifact_root not in destination.parents:
            raise ValueError("HTTP 证据 Artifact 路径不安全")
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(destination.write_bytes, body)
        artifact = self.repository.save_artifact(
            Artifact(
                thread_id=run.thread_id,
                # HTTP evidence belongs to the active Run as well as its Thread.  Without
                # this, historical reports cannot safely distinguish it from another Run.
                run_id=run.id,
                filename=f"localhost-response-{request.call_id}{suffix}",
                kind="http_evidence_truncated" if body_truncated else "http_evidence",
                sha256=hashlib.sha256(body).hexdigest(),
                size=len(body),
                mime_type=content_type.split(";", 1)[0] or "text/plain",
                storage_ref=storage_ref,
            )
        )
        return [artifact.id]

    @staticmethod
    async def _read_limited_body(response: httpx.Response) -> tuple[bytes, bool]:
        body = bytearray()
        truncated = False
        async for chunk in response.aiter_bytes():
            remaining = MAX_PROBE_RESPONSE_BYTES - len(body)
            if remaining <= 0:
                truncated = True
                break
            body.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
                break
        return bytes(body), truncated

    @staticmethod
    def _is_textual(content_type: str) -> bool:
        media_type = content_type.split(";", 1)[0].strip().casefold()
        return media_type.startswith("text/") or media_type in {
            "application/json",
            "application/javascript",
            "application/xml",
        }

    @staticmethod
    def _validate_local_url(raw_url: str) -> ParseResult:
        parsed = urlparse(raw_url)
        hostname = (parsed.hostname or "").casefold()
        if parsed.scheme != "http" or hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("本机 CTF HTTP 工具仅允许 http://localhost 或 http://127.0.0.1")
        if parsed.username or parsed.password:
            raise ValueError("本机 CTF HTTP 工具不允许 URL 用户名或密码")
        if any(part in {".", ".."} for part in parsed.path.split("/")):
            raise ValueError("本机 CTF HTTP 工具不允许路径穿越")
        return parsed

    @staticmethod
    def _loopback_request_url(raw_url: str, parsed: ParseResult) -> str:
        """固定回环解析；容器部署可经固定宿主机网关访问用户已授权的本机靶场。"""

        gateway = os.environ.get("YUWANG_LOCAL_CTF_HOST_GATEWAY", "").strip()
        if gateway:
            parsed_gateway = urlparse(gateway)
            if (
                parsed_gateway.scheme != "http"
                or parsed_gateway.hostname != "host.docker.internal"
                or parsed_gateway.username
                or parsed_gateway.password
                or parsed_gateway.path not in {"", "/"}
                or parsed_gateway.query
                or parsed_gateway.fragment
                or parsed_gateway.port is not None
            ):
                raise ValueError("本机 CTF 宿主机网关配置无效")
            hostname = parsed_gateway.hostname
        else:
            hostname = "127.0.0.1"
        netloc = hostname + (f":{parsed.port}" if parsed.port is not None else "")
        return parsed._replace(netloc=netloc).geturl()

    @classmethod
    def _validate_target_scope(cls, raw_url: str, target_scope: list[str]) -> None:
        parsed = cls._validate_local_url(raw_url)
        if not target_scope:
            raise ValueError("任务未声明本机 CTF HTTP 授权目标")
        for target in target_scope:
            scope = urlparse(target if "://" in target else f"//{target}")
            if not scope.hostname or scope.hostname.casefold() != (parsed.hostname or "").casefold():
                continue
            try:
                scope_port = scope.port
            except ValueError:
                continue
            if scope_port is None or scope_port == parsed.port:
                return
        raise ValueError("请求目标不在本次 Run 的授权范围")

    @staticmethod
    def _explicit_links(text: str, base_url: str, parsed_url: ParseResult) -> list[str]:
        if "<" not in text:
            return []
        parser = _ExplicitLinkParser()
        try:
            parser.feed(text)
        except Exception:
            return []
        links: list[str] = []
        for candidate in parser.links:
            resolved = urlparse(urljoin(base_url, candidate))
            if (
                resolved.scheme == "http"
                and resolved.hostname == parsed_url.hostname
                and resolved.port == parsed_url.port
                and resolved.path
            ):
                item = resolved.path + (f"?{resolved.query}" if resolved.query else "")
                if len(item) <= 2_048 and item not in links:
                    links.append(item)
        return links[:20]

    @staticmethod
    def _robots_paths(text: str, request_path: str) -> list[str]:
        if request_path != "/robots.txt":
            return []
        paths: list[str] = []
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            path = value.strip()
            if (
                separator
                and key.casefold() in {"allow", "disallow"}
                and path.startswith("/")
                and len(path) <= 2_048
            ):
                if path not in paths:
                    paths.append(path)
        return paths[:20]


def create_reference_registry(
    artifact_root: Path, repository: _HttpEvidenceRepository | None = None
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(FileMetadataTool(artifact_root))
    registry.register(LocalhostHTTPProbeTool(artifact_root, repository))
    return registry
