"""CTF 工具的受控 Artifact 访问层，禁止将宿主机路径暴露给工具输入。"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from yuwang.domain.models import Artifact, Run
from yuwang.tooling.contracts import ToolCallRequest


class ArtifactRepository(Protocol):
    def get_artifact(self, artifact_id: UUID | str) -> Artifact | None: ...
    def save_artifact(self, value: Artifact) -> Artifact: ...
    def get_run(self, run_id: UUID | str) -> Run | None: ...


class ArtifactAccess:
    """由 Artifact ID 定位、校验和写入题目文件，不提供任何路径参数入口。"""

    def __init__(self, repository: ArtifactRepository, root: Path) -> None:
        self.repository = repository
        self.root = root.resolve()

    def read(self, artifact_id: UUID, request: ToolCallRequest | None, *, max_bytes: int) -> tuple[Artifact, bytes]:
        artifact = self.repository.get_artifact(artifact_id)
        if not artifact:
            raise ValueError("Artifact 不存在")
        if not request or not request.run_id:
            raise ValueError("CTF 工具必须绑定当前 Run 才能读取 Artifact")
        run = self.repository.get_run(request.run_id)
        if not run or artifact.thread_id != run.thread_id:
            raise ValueError("Artifact 不属于当前 Thread")
        path = (self.root / artifact.storage_ref).resolve()
        if self.root not in path.parents or not path.is_file():
            raise ValueError("Artifact 数据不存在或路径不安全")
        if artifact.size > max_bytes:
            raise ValueError("Artifact 超过该工具允许的读取大小")
        data = path.read_bytes()
        if len(data) > max_bytes:
            raise ValueError("Artifact 内容超过该工具允许的读取大小")
        return artifact, data

    def create(
        self,
        parent: Artifact,
        *,
        filename: str,
        content: bytes,
        kind: str,
        mime_type: str | None = None,
        run_id: UUID | None = None,
    ) -> Artifact:
        """生成派生 Artifact；文件名只保留基名，存储引用由服务端生成。"""

        safe_name = Path(filename).name
        if safe_name in {"", ".", ".."}:
            raise ValueError("派生 Artifact 文件名无效")
        artifact_id = uuid4()
        storage_ref = f"{parent.thread_id}/{artifact_id}.blob"
        destination = (self.root / storage_ref).resolve()
        if self.root not in destination.parents:
            raise ValueError("派生 Artifact 存储路径不安全")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return self.repository.save_artifact(
            Artifact(
                id=artifact_id,
                thread_id=parent.thread_id,
                run_id=run_id or parent.run_id,
                filename=safe_name,
                kind=kind,
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
                mime_type=mime_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
                storage_ref=storage_ref,
            )
        )
