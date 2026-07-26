"""首批低风险 CTF 工具的真实执行与 Artifact 边界测试。"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread as WorkerThread
from urllib.parse import urlparse
from uuid import UUID

import pytest

from yuwang.domain.models import Artifact, Run, Thread
from yuwang.storage import SQLiteRepository
from yuwang.tooling import ToolCallRequest, ToolExecutor, ToolRegistry, create_reference_registry
from yuwang.tooling.builtins import LocalhostHTTPProbeTool
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


def test_localhost_probe_uses_only_the_fixed_docker_host_gateway(monkeypatch) -> None:
    raw_url = "http://127.0.0.1:8088/robots.txt"
    parsed = urlparse(raw_url)
    monkeypatch.setenv("YUWANG_LOCAL_CTF_HOST_GATEWAY", "http://host.docker.internal")

    assert LocalhostHTTPProbeTool._loopback_request_url(raw_url, parsed) == (
        "http://host.docker.internal:8088/robots.txt"
    )

    monkeypatch.setenv("YUWANG_LOCAL_CTF_HOST_GATEWAY", "http://127.0.0.1")
    with pytest.raises(ValueError, match="宿主机网关配置无效"):
        LocalhostHTTPProbeTool._loopback_request_url(raw_url, parsed)


async def invoke(executor: ToolExecutor, run: Run, tool: str, arguments: dict[str, object]):
    return await executor.execute_call(
        ToolCallRequest(
            run_id=run.id,
            tool_id=f"ctf.{tool}",
            tool_version="1.0.0",
            arguments=arguments,
        )
    )


@contextmanager
def local_ctf_server():
    """测试专用的公开线索链路，不在生产路径注册或暴露。"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path, _, query = self.path.partition("?")
            if path == "/":
                self._reply(
                    200,
                    "text/html; charset=utf-8",
                    b'<meta name="build-token" content="sunrise-7"><a href="/api/status">status</a>',
                )
            elif path == "/api/status":
                self._reply(200, "application/json", b'{"next":"/robots.txt"}')
            elif path == "/robots.txt":
                self._reply(200, "text/plain", b"User-agent: *\nDisallow: /dev-notes.txt\n")
            elif path == "/dev-notes.txt":
                self._reply(
                    200,
                    "text/plain",
                    b"Debug requires query unlock=1 and header X-CTF-Token equal to the build token.",
                )
            elif (
                path == "/api/debug"
                and query == "unlock=1"
                and self.headers.get("X-CTF-Token") == "sunrise-7"
            ):
                self._reply(
                    200,
                    "application/json",
                    json.dumps(
                        {"flag_b64": "ZmxhZ3tsb2NhbF9hZ2VudF9mb3VuZF9kZWJ1Z19kb29yfQ=="}
                    ).encode(),
                )
            else:
                self._reply(403, "text/plain", b"forbidden")

        def _reply(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = WorkerThread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()


async def invoke_http(
    executor: ToolExecutor,
    run: Run,
    base_url: str,
    path: str,
    *,
    ctf_header: dict[str, str] | None = None,
):
    return await executor.execute_call(
        ToolCallRequest(
            run_id=run.id,
            tool_id="builtin.localhost_http_probe",
            tool_version="1.1.0",
            target_scope=[base_url],
            arguments={
                "url": f"{base_url}{path}",
                **({"ctf_header": ctf_header} if ctf_header else {}),
            },
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
async def test_encoding_decode_accepts_bounded_inline_text_and_preserves_artifact_boundary(
    tmp_path: Path,
) -> None:
    repository, _, thread, artifact, run, executor = setup_tool_context(tmp_path, b"placeholder")

    decoded = await invoke(
        executor,
        run,
        "encoding_decode",
        {"text": "SGVsbG8=", "encoding": "base64"},
    )
    neither = await invoke(executor, run, "encoding_decode", {"encoding": "base64"})
    both = await invoke(
        executor,
        run,
        "encoding_decode",
        {"artifact_id": str(artifact.id), "text": "SGVsbG8=", "encoding": "base64"},
    )
    pointer = await invoke(
        executor,
        run,
        "encoding_decode",
        {"text": "SGVsbG8=", "json_pointer": "/value"},
    )

    assert decoded.success
    assert decoded.output["candidates"] == [
        {"value": "Hello", "preview": "Hello", "confidence": 0.96, "decode_chain": ["base64"]}
    ]
    assert not neither.success and neither.error and neither.error.code == "invalid_input"
    assert not both.success and both.error and both.error.code == "invalid_input"
    assert not pointer.success and pointer.error and pointer.error.code == "invalid_input"
    assert repository.list_artifacts(thread.id) == [artifact]

    long_text = base64.b64encode(b"x" * 2_001).decode()
    long_result = await invoke(
        executor,
        run,
        "encoding_decode",
        {"text": long_text, "encoding": "base64"},
    )
    assert long_result.success
    created = repository.get_artifact(UUID(long_result.output["artifact_ids"][0]))
    assert created and created.thread_id == thread.id and created.run_id == run.id
    assert created.kind == "decoded_text" and created.size == 2_001


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


@pytest.mark.asyncio
async def test_local_ctf_http_evidence_chain_decodes_candidate_flag(tmp_path: Path) -> None:
    repository, root, _, _, run, _ = setup_tool_context(tmp_path, b"placeholder")
    registry = create_reference_registry(root, repository)
    register_ctf_tools(registry, repository, root)
    executor = ToolExecutor(registry)

    with local_ctf_server() as base_url:
        blocked_scope = await executor.execute_call(
            ToolCallRequest(
                run_id=run.id,
                tool_id="builtin.localhost_http_probe",
                tool_version="1.1.0",
                target_scope=["http://127.0.0.1:1"],
                arguments={"url": f"{base_url}/"},
            )
        )
        homepage = await invoke_http(executor, run, base_url, "/")
        status = await invoke_http(executor, run, base_url, "/api/status")
        robots = await invoke_http(executor, run, base_url, "/robots.txt")
        notes = await invoke_http(executor, run, base_url, "/dev-notes.txt")
        debug = await invoke_http(
            executor,
            run,
            base_url,
            "/api/debug?unlock=1",
            ctf_header={"name": "X-CTF-Token", "value": "sunrise-7"},
        )

    assert homepage.success and "build-token" in homepage.output["body_excerpt"]
    assert not blocked_scope.success
    assert blocked_scope.error and "授权范围" in blocked_scope.error.message
    assert homepage.output["explicit_links"] == ["/api/status"]
    assert status.success and "robots.txt" in status.output["body_excerpt"]
    assert robots.success and robots.output["robots_paths"] == ["/dev-notes.txt"]
    assert notes.success and "X-CTF-Token" in notes.output["body_excerpt"]
    assert debug.success and debug.output["artifact_ids"]

    evidence_id = debug.output["artifact_ids"][0]
    decoded = await invoke(
        executor,
        run,
        "encoding_decode",
        {"artifact_id": evidence_id, "encoding": "base64", "json_pointer": "/flag_b64"},
    )
    candidate = decoded.output["candidates"][0]["value"]
    verified = await invoke(
        executor,
        run,
        "flag_candidate_verify",
        {"artifact_id": evidence_id, "candidate": candidate, "flag_prefix": "flag"},
    )

    assert candidate == "flag{local_agent_found_debug_door}"
    assert verified.success
    assert verified.output["validation_status"] == "format_matched"

    invalid_header = await executor.execute_call(
        ToolCallRequest(
            run_id=run.id,
            tool_id="builtin.localhost_http_probe",
            tool_version="1.1.0",
            target_scope=["127.0.0.1"],
            arguments={
                "url": "http://127.0.0.1:8088/",
                "ctf_header": {"name": "Authorization", "value": "Bearer forbidden"},
            },
        )
    )
    assert not invalid_header.success
    assert invalid_header.error and invalid_header.error.code == "invalid_input"
