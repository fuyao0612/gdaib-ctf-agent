"""首批低风险 CTF 工具的真实执行与 Artifact 边界测试。"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from uuid import UUID

import pytest

from yuwang.domain.models import Artifact, Run, Thread
from yuwang.storage import SQLiteRepository
from yuwang.tooling import ToolCallRequest, ToolExecutor, ToolRegistry
from yuwang.tooling.ctf import register_ctf_tools


def setup_tool_context(tmp_path: Path, content: bytes, filename: str = "challenge.bin"):
    root = tmp_path / "artifacts"
    root.mkdir(parents=True)
    repository = SQLiteRepository(tmp_path / "ctf.db")
    thread = repository.save_thread(Thread(title="CTF 工具测试"))
    storage_ref = f"{thread.id}/upload.blob"
    path = root / storage_ref
    path.parent.mkdir()
    path.write_bytes(content)
    artifact = repository.save_artifact(
        Artifact(
            thread_id=thread.id,
            filename=filename,
            kind="upload",
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            mime_type="application/octet-stream",
            storage_ref=storage_ref,
        )
    )
    run = repository.save_run(Run(thread_id=thread.id))
    registry = ToolRegistry()
    register_ctf_tools(registry, repository, root)
    return repository, root, thread, artifact, run, ToolExecutor(registry)


async def invoke(executor: ToolExecutor, run: Run, tool: str, arguments: dict[str, object]):
    return await executor.execute_call(
        ToolCallRequest(
            run_id=run.id,
            tool_id=f"ctf.{tool}",
            tool_version="1.0.0",
            arguments=arguments,
        )
    )


@pytest.mark.asyncio
async def test_encoding_and_flag_candidate_are_artifact_bound(tmp_path: Path) -> None:
    _, _, _, artifact, run, executor = setup_tool_context(tmp_path, b"ZmxhZ3tkZWNvZGVkX2N0Zn0=")

    decoded = await invoke(
        executor, run, "encoding_decode", {"artifact_id": str(artifact.id), "encoding": "base64"}
    )
    flag = await invoke(
        executor,
        run,
        "flag_candidate_verify",
        {"artifact_id": str(artifact.id), "candidate": "flag{decoded_ctf}", "flag_prefix": "flag"},
    )
    arbitrary_path = await invoke(
        executor,
        run,
        "file_inspect",
        {"artifact_id": str(artifact.id), "path": "C:\\Windows\\win.ini"},
    )

    assert decoded.success
    assert decoded.output["candidates"][0]["value"] == "flag{decoded_ctf}"
    assert flag.success
    assert flag.output["validation_status"] == "format_matched"
    assert "尚未经过赛题平台验证" in flag.output["message"]
    assert not arbitrary_path.success
    assert arbitrary_path.error and arbitrary_path.error.code == "invalid_input"


@pytest.mark.asyncio
async def test_file_inspect_and_strings_extract_create_real_artifact(tmp_path: Path) -> None:
    content = b"\x7fELF\x00junk FLAG{ascii_value}\x00" + "UTF16_FLAG{value}".encode("utf-16le")
    repository, _, _, artifact, run, executor = setup_tool_context(tmp_path, content, "sample.elf")

    inspected = await invoke(executor, run, "file_inspect", {"artifact_id": str(artifact.id)})
    strings = await invoke(
        executor,
        run,
        "strings_extract",
        {"artifact_id": str(artifact.id), "min_length": 4, "max_results": 20},
    )

    assert inspected.success
    assert inspected.output["file_signature"] == "ELF executable"
    assert inspected.output["sha256"] == hashlib.sha256(content).hexdigest()
    assert strings.success
    assert any("FLAG{ascii_value}" in item for item in strings.output["preview"])
    derived_id = UUID(strings.output["artifact_ids"][0])
    assert strings.artifact_ids == [str(derived_id)]
    derived = repository.get_artifact(derived_id)
    assert derived and derived.kind == "strings_result"


@pytest.mark.asyncio
async def test_archive_extract_rejects_zip_slip_and_creates_child_artifacts(tmp_path: Path) -> None:
    good_buffer = io.BytesIO()
    with zipfile.ZipFile(good_buffer, "w") as archive:
        archive.writestr("nested/flag.txt", "flag{from_archive}")
    repository, _, _, artifact, run, executor = setup_tool_context(tmp_path, good_buffer.getvalue(), "good.zip")

    extracted = await invoke(executor, run, "archive_extract", {"artifact_id": str(artifact.id)})

    assert extracted.success
    assert extracted.output["extracted_names"] == ["nested/flag.txt"]
    child = repository.get_artifact(UUID(extracted.output["artifact_ids"][0]))
    assert child and child.kind == "archive_extract"
    assert extracted.artifact_ids == extracted.output["artifact_ids"]

    bad_buffer = io.BytesIO()
    with zipfile.ZipFile(bad_buffer, "w") as archive:
        archive.writestr("../escape.txt", "unsafe")
    _, _, _, bad_artifact, bad_run, bad_executor = setup_tool_context(tmp_path / "bad", bad_buffer.getvalue(), "bad.zip")
    rejected = await invoke(bad_executor, bad_run, "archive_extract", {"artifact_id": str(bad_artifact.id)})
    assert not rejected.success
    assert rejected.error and "不安全路径" in rejected.error.message


@pytest.mark.asyncio
async def test_classical_cipher_is_bounded_and_artifact_scope_is_enforced(tmp_path: Path) -> None:
    repository, root, _, artifact, run, executor = setup_tool_context(tmp_path, b"gur synt vf uvqqra")

    analyzed = await invoke(
        executor,
        run,
        "classical_cipher_analyze",
        {"artifact_id": str(artifact.id), "methods": ["rot13"], "max_candidates": 1},
    )

    other_thread = repository.save_thread(Thread(title="其他对话"))
    foreign_ref = f"{other_thread.id}/foreign.blob"
    foreign_path = root / foreign_ref
    foreign_path.parent.mkdir(exist_ok=True)
    foreign_path.write_bytes(b"foreign")
    foreign = repository.save_artifact(
        Artifact(
            thread_id=other_thread.id,
            filename="foreign.txt",
            kind="upload",
            sha256=hashlib.sha256(b"foreign").hexdigest(),
            size=7,
            mime_type="text/plain",
            storage_ref=foreign_ref,
        )
    )
    cross_thread = await invoke(executor, run, "file_inspect", {"artifact_id": str(foreign.id)})

    assert analyzed.success
    assert analyzed.output["candidates"] == [
        {"method": "rot13", "key": "shift=13", "preview": "the flag is hidden", "score": analyzed.output["candidates"][0]["score"]}
    ]
    assert not cross_thread.success
    assert cross_thread.error and "不属于当前 Thread" in cross_thread.error.message
