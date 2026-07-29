from __future__ import annotations

import hashlib
from pathlib import Path

from yuwang.domain.models import Artifact, Run, Thread
from yuwang.storage import SQLiteRepository
from yuwang.storage.legacy_import import import_legacy_data


def test_legacy_import_merges_existing_volume_and_is_idempotent(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    volume_root = tmp_path / "volume"
    legacy_artifacts = legacy_root / "artifacts"
    volume_artifacts = volume_root / "artifacts"
    legacy_artifacts.mkdir(parents=True)
    volume_artifacts.mkdir(parents=True)
    legacy = SQLiteRepository(legacy_root / "yuwang.db")
    existing = SQLiteRepository(volume_root / "yuwang.db")

    legacy_thread = legacy.save_thread(Thread(title="宿主历史"))
    legacy.save_run(Run(thread_id=legacy_thread.id))
    content = b"host artifact"
    artifact = legacy.save_artifact(
        Artifact(
            thread_id=legacy_thread.id, filename="evidence.txt", kind="http_evidence",
            sha256=hashlib.sha256(content).hexdigest(), size=len(content), mime_type="text/plain",
            storage_ref=f"{legacy_thread.id}/evidence.txt",
        )
    )
    source_file = legacy_artifacts / artifact.storage_ref
    source_file.parent.mkdir()
    source_file.write_bytes(content)

    existing_thread = existing.save_thread(Thread(title="卷内历史"))
    existing.save_run(Run(thread_id=existing_thread.id))

    assert import_legacy_data(
        legacy_root / "yuwang.db", volume_root / "yuwang.db", legacy_artifacts, volume_artifacts
    )
    merged = SQLiteRepository(volume_root / "yuwang.db")
    assert {thread.title for thread in merged.list_threads()} == {"宿主历史", "卷内历史"}
    assert merged.get_artifact(artifact.id) == artifact
    assert (volume_artifacts / artifact.storage_ref).read_bytes() == content
    assert (volume_root / ".legacy-host-import-v1").exists()

    merged.save_thread(Thread(title="首次迁移后的卷内数据"))
    assert not import_legacy_data(
        legacy_root / "yuwang.db", volume_root / "yuwang.db", legacy_artifacts, volume_artifacts
    )
    assert len(SQLiteRepository(volume_root / "yuwang.db").list_threads()) == 3
