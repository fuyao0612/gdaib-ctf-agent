"""受限 ZIP/TAR 解包工具，明确拒绝路径穿越、链接和压缩炸弹。"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import PurePosixPath
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from yuwang.tooling.contracts import ToolCallRequest, ToolSpec

from .base import CtfArtifactTool, ctf_spec

MAX_FILES = 100
MAX_SINGLE_FILE = 4 * 1024 * 1024
MAX_TOTAL_SIZE = 20 * 1024 * 1024
MAX_RATIO = 100


class ArchiveExtractInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    max_files: int = Field(default=50, ge=1, le=MAX_FILES)
    max_total_size: int = Field(default=10 * 1024 * 1024, ge=1, le=MAX_TOTAL_SIZE)
    recursive_depth: int = Field(default=1, ge=1, le=1)


class ArchiveExtractOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extracted_count: int = Field(ge=0)
    extracted_names: list[str] = Field(default_factory=list, max_length=MAX_FILES)
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=MAX_FILES)


class ArchiveExtractTool(CtfArtifactTool[ArchiveExtractInput, ArchiveExtractOutput]):
    input_model = ArchiveExtractInput
    output_model = ArchiveExtractOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="archive_extract",
            display_name="安全解包归档文件",
            description="从上传 ZIP 或 TAR Artifact 解出普通文件，拒绝 Zip Slip、链接、设备文件和疑似压缩炸弹",
            capabilities=["archive", "file", "extract"],
            scenarios=["ctf", "forensics"],
            permissions=["artifact:read", "artifact:create"],
            timeout_seconds=30,
            error_codes=["unsupported_archive", "unsafe_archive_entry", "archive_limit_exceeded"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
            artifact_types=["archive_extract"],
        )

    async def execute_with_request(
        self, value: ArchiveExtractInput, request: ToolCallRequest | None
    ) -> ArchiveExtractOutput:
        artifact, content = self.artifacts.read(value.artifact_id, request, max_bytes=8 * 1024 * 1024)
        entries = _archive_entries(content, value.max_files, value.max_total_size)
        artifact_ids: list[UUID] = []
        names: list[str] = []
        for index, (name, data) in enumerate(entries, start=1):
            created = self.artifacts.create(
                artifact,
                filename=f"extract-{index}-{PurePosixPath(name).name}",
                content=data,
                kind="archive_extract",
                run_id=request.run_id if request else None,
            )
            artifact_ids.append(created.id)
            names.append(name)
        return ArchiveExtractOutput(
            extracted_count=len(artifact_ids), extracted_names=names, artifact_ids=artifact_ids
        )


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return bool(name) and not path.is_absolute() and ".." not in path.parts and path.name not in {"", ".", ".."}


def _check_limits(count: int, size: int, compressed: int, max_files: int, max_total: int) -> None:
    if count > max_files:
        raise ValueError("归档文件数量超过限制")
    if size > MAX_SINGLE_FILE:
        raise ValueError("归档内单文件超过限制")
    if size > 0 and compressed <= 0:
        raise ValueError("归档条目压缩数据异常")
    if compressed > 0 and size / compressed > MAX_RATIO:
        raise ValueError("疑似压缩炸弹：压缩比超过限制")
    if max_total < size:
        raise ValueError("归档解包总大小超过限制")


def _archive_entries(content: bytes, max_files: int, max_total_size: int) -> list[tuple[str, bytes]]:
    if zipfile.is_zipfile(io.BytesIO(content)):
        return _zip_entries(content, max_files, max_total_size)
    try:
        return _tar_entries(content, max_files, max_total_size)
    except (tarfile.TarError, EOFError):
        raise ValueError("仅支持 ZIP 与 TAR 系列归档") from None


def _zip_entries(content: bytes, max_files: int, max_total_size: int) -> list[tuple[str, bytes]]:
    entries: list[tuple[str, bytes]] = []
    remaining = max_total_size
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            is_link = (item.external_attr >> 16) & 0o170000 == 0o120000
            if is_link or not _safe_name(item.filename):
                raise ValueError("归档包含不安全路径或符号链接")
            _check_limits(len(entries) + 1, item.file_size, item.compress_size, max_files, remaining)
            data = archive.read(item)
            if len(data) != item.file_size:
                raise ValueError("归档条目大小不一致")
            remaining -= len(data)
            entries.append((item.filename, data))
    return entries


def _tar_entries(content: bytes, max_files: int, max_total_size: int) -> list[tuple[str, bytes]]:
    entries: list[tuple[str, bytes]] = []
    remaining = max_total_size
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
        for item in archive.getmembers():
            if item.isdir():
                continue
            if not item.isfile() or not _safe_name(item.name):
                raise ValueError("归档包含非普通文件或不安全路径")
            _check_limits(len(entries) + 1, item.size, item.size, max_files, remaining)
            stream = archive.extractfile(item)
            if stream is None:
                raise ValueError("无法读取归档条目")
            data = stream.read(MAX_SINGLE_FILE + 1)
            if len(data) != item.size or len(data) > MAX_SINGLE_FILE:
                raise ValueError("归档条目大小不一致或超过限制")
            remaining -= len(data)
            entries.append((item.name, data))
    return entries
