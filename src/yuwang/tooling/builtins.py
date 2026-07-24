"""平台内置的低风险参考工具。"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from pathlib import Path

import httpx
from pydantic import BaseModel

from .contracts import ToolSpec
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


class ProbeInput(BaseModel):
    url: str


class ProbeOutput(BaseModel):
    status_code: int
    content_type: str


class LocalhostHTTPProbeTool(ToolPlugin[ProbeInput, ProbeOutput]):
    input_model = ProbeInput
    output_model = ProbeOutput

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="localhost_http_probe",
            version="1.0.0",
            description="仅探测经策略批准的本地 HTTP 服务",
            capabilities=["http", "metadata"],
            scenarios=["general"],
            risk="medium",
            permissions=["network:localhost"],
            requires_network=True,
            allowed_target_types=["localhost"],
            timeout_seconds=5,
            error_codes=["request_failed"],
            idempotent=True,
            artifact_types=[],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute(self, value: ProbeInput) -> ProbeOutput:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.get(value.url)
        return ProbeOutput(
            status_code=response.status_code, content_type=response.headers.get("content-type", "")
        )


def create_reference_registry(artifact_root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(FileMetadataTool(artifact_root))
    registry.register(LocalhostHTTPProbeTool())
    return registry
