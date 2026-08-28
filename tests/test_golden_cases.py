from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import yaml  # type: ignore[import-untyped]

from scripts.init_golden_cases import create_attachment_case
from scripts.run_golden_demo import _case_config, _sse_payload


def test_golden_case_manifests_are_declarative_and_do_not_expose_private_judges():
    root = Path("docs/golden-cases")
    manifests = list(root.glob("*/manifest.yaml"))

    assert {path.parent.name for path in manifests} == {
        "A-ctf-attachment", "B-local-web", "C-prompt-injection"
    }
    for path in manifests:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert manifest["allowed_tools"]
        assert manifest["max_attempts"] >= 1
        assert manifest["timeout"] > 0
        assert "expected_sha256" not in path.read_text(encoding="utf-8")
        assert (path.parent / "verifier" / manifest["judge"]).is_file()


def test_golden_attachment_initializer_generates_safe_separate_input(tmp_path: Path):
    target = create_attachment_case(tmp_path)

    assert target.is_file()
    assert target.read_bytes().startswith(b"PK\x03\x04")


def test_golden_demo_loader_keeps_answers_out_of_prompts_and_manifest(tmp_path: Path):
    manifest, prompt, inputs = _case_config("A-ctf-attachment", tmp_path)

    assert manifest["case_id"] == "golden-ctf-attachment"
    assert "flag{" not in prompt
    assert len(inputs) == 1 and inputs[0].is_file()
    assert "expected_sha256" not in str(manifest)


def test_golden_demo_reads_only_formal_message_sse_payload():
    class Response:
        text = "event: execution_started\ndata: {\"run\": {\"id\": \"run-1\"}}\n\n"

    assert _sse_payload(Response()) == {"run": {"id": "run-1"}}


def test_golden_web_lab_only_binds_loopback_and_exposes_multistep_clues():
    process = subprocess.Popen(
        [sys.executable, "scripts/golden_web_lab.py", "--port", "18088"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(20):
            try:
                with urlopen("http://127.0.0.1:18088/", timeout=0.2) as response:
                    body = response.read().decode()
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("本地 Web 靶场未启动")
        assert "/api/status" in body
        with urlopen("http://127.0.0.1:18088/robots.txt", timeout=1) as response:
            assert "/dev-notes.txt" in response.read().decode()
        with urlopen("http://127.0.0.1:18088/dev-notes.txt", timeout=1) as response:
            assert "/api/debug?unlock=1" in response.read().decode()
    finally:
        process.terminate()
        process.wait(timeout=3)
