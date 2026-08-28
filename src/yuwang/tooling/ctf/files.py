"""文件基础检查与字符串提取，始终从已上传 Artifact 读取。"""

from __future__ import annotations

import base64
import hashlib
import math
import mimetypes
import re
from collections import Counter
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from yuwang.tooling.contracts import ToolCallRequest, ToolSpec
from yuwang.tooling.runtime import SandboxRequest, SandboxRuntime

from .artifacts import ArtifactAccess
from .base import CtfArtifactTool, ctf_spec

PRINTABLE = re.compile(rb"[\x20-\x7e]{4,}")
UTF16_LE = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")
UTF16_BE = re.compile(rb"(?:\x00[\x20-\x7e]){4,}")


class FileInspectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID


class FileInspectOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    sha256: str
    size: int
    mime_type: str
    file_signature: str
    entropy: float = Field(ge=0, le=8)
    printable_strings_preview: list[str] = Field(default_factory=list, max_length=20)
    artifact_ids: list[UUID] = Field(default_factory=list)


class StringsExtractInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    min_length: int = Field(default=4, ge=3, le=128)
    max_results: int = Field(default=500, ge=1, le=2_000)


class StringsExtractOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=0)
    preview: list[str] = Field(default_factory=list, max_length=40)
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=1)


class FileInspectTool(CtfArtifactTool[FileInspectInput, FileInspectOutput]):
    input_model = FileInspectInput
    output_model = FileInspectOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="file_inspect",
            display_name="文件安全检查",
            description="计算上传题目 Artifact 的哈希、类型、签名、熵和可打印字符串摘要，不执行文件",
            capabilities=["file", "metadata", "forensics"],
            scenarios=["ctf", "forensics"],
            permissions=["artifact:read"],
            timeout_seconds=10,
            error_codes=["artifact_not_found", "file_too_large"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(
        self, value: FileInspectInput, request: ToolCallRequest | None
    ) -> FileInspectOutput:
        artifact, content = self.artifacts.read(value.artifact_id, request, max_bytes=8 * 1024 * 1024)
        return FileInspectOutput(
            artifact_id=artifact.id,
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            mime_type=artifact.mime_type or mimetypes.guess_type(artifact.filename)[0] or "application/octet-stream",
            file_signature=_signature(content),
            entropy=round(_entropy(content), 4),
            printable_strings_preview=[item.decode("ascii", errors="replace")[:160] for item in PRINTABLE.findall(content)[:20]],
        )


class StringsExtractTool(CtfArtifactTool[StringsExtractInput, StringsExtractOutput]):
    input_model = StringsExtractInput
    output_model = StringsExtractOutput

    def __init__(self, artifacts: ArtifactAccess, sandbox_runtime: SandboxRuntime | None = None) -> None:
        super().__init__(artifacts)
        self.sandbox_runtime = sandbox_runtime

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="strings_extract",
            display_name="提取可打印字符串",
            description="从上传 Artifact 提取 ASCII、UTF-8 和 UTF-16 可打印字符串，并将完整结果写入派生 Artifact",
            capabilities=["file", "strings", "forensics"],
            scenarios=["ctf", "forensics", "reverse"],
            permissions=["artifact:read", "artifact:create"],
            timeout_seconds=20,
            error_codes=["artifact_not_found", "file_too_large", "result_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
            artifact_types=["strings_result"],
        )

    async def execute_with_request(
        self, value: StringsExtractInput, request: ToolCallRequest | None
    ) -> StringsExtractOutput:
        artifact, content = self.artifacts.read(
            value.artifact_id,
            request,
            max_bytes=5 * 1024 * 1024,
        )
        if self.sandbox_runtime is None:
            # 独立 SDK/单元测试仍可显式不注入运行时；正式 API 始终注入 Docker 沙箱。
            strings = _extract_strings(content, value.min_length, value.max_results)
        else:
            result = await self.sandbox_runtime.execute(
                SandboxRequest(
                    operation="extract_strings",
                    payload_base64=base64.b64encode(content).decode("ascii"),
                    min_length=value.min_length,
                    max_results=value.max_results,
                )
            )
            raw_strings = result.get("strings")
            if not isinstance(raw_strings, list) or not all(
                isinstance(item, str) for item in raw_strings
            ):
                raise ValueError("Docker 工具沙箱返回的字符串结果无效")
            strings = raw_strings[: value.max_results]
        payload = ("\n".join(strings) + ("\n" if strings else "")).encode("utf-8")
        created = self.artifacts.create(
            artifact,
            filename=f"{artifact.filename}.strings.txt",
            content=payload,
            kind="strings_result",
            mime_type="text/plain",
            run_id=request.run_id if request else None,
        )
        return StringsExtractOutput(count=len(strings), preview=strings[:40], artifact_ids=[created.id])


def _signature(content: bytes) -> str:
    signatures = (
        (b"\x7fELF", "ELF executable"),
        (b"PK\x03\x04", "ZIP archive"),
        (b"\x1f\x8b", "GZIP archive"),
        (b"\x89PNG\r\n\x1a\n", "PNG image"),
        (b"GIF87a", "GIF image"),
        (b"GIF89a", "GIF image"),
        (b"%PDF-", "PDF document"),
    )
    for prefix, label in signatures:
        if content.startswith(prefix):
            return label
    if len(content) >= 262 and content[257:262] == b"ustar":
        return "TAR archive"
    return "unknown"


def _entropy(content: bytes) -> float:
    if not content:
        return 0.0
    size = len(content)
    # 单次遍历统计，避免对攻击者控制的附件执行 256 次全量扫描并阻塞 API 事件循环。
    frequencies = Counter(content).values()
    return -sum((count / size) * math.log2(count / size) for count in frequencies if count)


def _extract_strings(content: bytes, minimum: int, maximum: int) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    ascii_pattern = re.compile(rb"[\x20-\x7e]{" + str(minimum).encode() + rb",}")
    for match in ascii_pattern.finditer(content):
        value = match.group().decode("ascii", errors="replace")
        if value not in seen:
            seen.add(value)
            values.append(value)
            if len(values) >= maximum:
                return values
    wide_patterns = (
        (re.compile(rb"(?:[\x20-\x7e]\x00){" + str(minimum).encode() + rb",}"), "utf-16le", 0),
        (re.compile(rb"(?:\x00[\x20-\x7e]){" + str(minimum).encode() + rb",}"), "utf-16be", 1),
    )
    candidates: list[tuple[int, int, int, str]] = []
    for pattern, encoding, order in wide_patterns:
        candidates.extend(
            (match.start(), match.end(), order, match.group().decode(encoding, errors="replace"))
            for match in pattern.finditer(content)
        )
    for start, end, _order, value in sorted(candidates, key=lambda item: (item[2], item[0])):
        if any(
            other_start <= start
            and other_end >= end
            and other_end - other_start > end - start
            for other_start, other_end, _, _ in candidates
        ):
            continue
        if value not in seen:
            seen.add(value)
            values.append(value)
            if len(values) >= maximum:
                return values
    return values
