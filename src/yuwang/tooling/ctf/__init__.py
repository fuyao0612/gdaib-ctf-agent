"""第一批低风险、仅面向已上传 CTF Artifact 的内置工具。"""

from __future__ import annotations

from pathlib import Path

from yuwang.tooling.registry import ToolRegistry
from yuwang.tooling.runtime import SandboxRuntime

from .analysis import (
    ArtifactSearchTool,
    BinaryStaticAnalyzeTool,
    InterfaceDocAnalyzeTool,
    IOCExtractTool,
    SourcePatternAnalyzeTool,
    WebEvidenceAnalyzeTool,
)
from .archive import ArchiveExtractTool
from .artifacts import ArtifactAccess, ArtifactRepository
from .ciphers import ClassicalCipherAnalyzeTool
from .encoding import EncodingDecodeTool
from .files import FileInspectTool, StringsExtractTool
from .flag import FlagCandidateVerifyTool


def register_ctf_tools(
    registry: ToolRegistry,
    repository: ArtifactRepository,
    artifact_root: Path,
    *,
    sandbox_runtime: SandboxRuntime | None = None,
) -> None:
    """显式注册，不扫描文件系统或导入未知代码。"""

    artifacts = ArtifactAccess(repository, artifact_root)
    for tool in (
        EncodingDecodeTool(artifacts),
        FileInspectTool(artifacts),
        StringsExtractTool(artifacts, sandbox_runtime),
        ArchiveExtractTool(artifacts),
        FlagCandidateVerifyTool(artifacts),
        ClassicalCipherAnalyzeTool(artifacts),
        IOCExtractTool(artifacts),
        ArtifactSearchTool(artifacts),
        SourcePatternAnalyzeTool(artifacts),
        BinaryStaticAnalyzeTool(artifacts),
        InterfaceDocAnalyzeTool(artifacts),
        WebEvidenceAnalyzeTool(artifacts),
    ):
        registry.register(tool)


__all__ = ["register_ctf_tools"]
