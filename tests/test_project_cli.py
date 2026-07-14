"""Windows 统一入口的安全与调度测试。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="仅在 Windows 验证 PowerShell 入口")


def run_cli(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """以干净的 PowerShell 5.1 进程运行根入口，保证编码和参数解析真实生效。"""
    result = subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "yuwang.ps1"),
            *arguments,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return result


def read_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            values[name] = value
    return values


def test_help_lists_every_supported_command_and_check_dispatch() -> None:
    output = run_cli("help").stdout
    for command in ("setup", "start", "stop", "status", "doctor", "check", "help"):
        assert f"yuwang.ps1 {command}" in output
    source = (ROOT / "yuwang.ps1").read_text(encoding="utf-8-sig")
    assert "'check' { & (Join-Path $root 'scripts\\full-check.ps1') }" in source


def test_setup_preserves_existing_environment_file() -> None:
    env_file = ROOT / ".env"
    before = env_file.read_bytes()
    output = run_cli("setup").stdout
    assert "不会覆盖密钥" in output
    assert env_file.read_bytes() == before


def test_status_reports_service_database_provider_and_agent() -> None:
    output = run_cli("status").stdout
    for label in ("Web", "API", "数据库", "Provider", "默认 Agent"):
        assert label in output


def test_doctor_is_read_only_and_never_prints_secrets() -> None:
    env_file = ROOT / ".env"
    process_file = ROOT / "data" / "dev-processes.json"
    before_env = env_file.read_bytes()
    before_process = process_file.read_bytes() if process_file.exists() else None
    before_coverage = {path.name for path in ROOT.glob(".coverage.*")}
    before_status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True, encoding="utf-8"
    )

    result = run_cli("doctor")
    output = result.stdout + result.stderr
    values = read_env_values()
    for name in ("YUWANG_ADMIN_TOKEN", "YUWANG_MASTER_KEY"):
        value = values.get(name, "")
        if value:
            assert value not in output
    assert "只读诊断" in output and "诊断汇总" in output
    assert env_file.read_bytes() == before_env
    assert (process_file.read_bytes() if process_file.exists() else None) == before_process
    assert {path.name for path in ROOT.glob(".coverage.*")} == before_coverage
    after_status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True, encoding="utf-8"
    )
    assert after_status == before_status


def test_start_check_only_uses_existing_development_checks() -> None:
    output = run_cli("start", "-Development", "-CheckOnly").stdout
    assert "本地开发环境检查通过" in output


def test_stop_refuses_reused_pid_and_does_not_kill_it() -> None:
    process_file = ROOT / "data" / "dev-processes.json"
    previous = process_file.read_bytes() if process_file.exists() else None
    process_file.parent.mkdir(parents=True, exist_ok=True)
    process_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_root": str(ROOT),
                "created_at": "1970-01-01T00:00:00Z",
                "processes": [
                    {
                        "role": "api",
                        "pid": os.getpid(),
                        "started_at": "1970-01-01T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    try:
        result = run_cli("stop", "-Development", check=False)
        assert result.returncode != 0
        assert "为防止误杀" in (result.stdout + result.stderr)
        assert os.getpid() > 0  # 若被误杀，pytest 不可能执行到这里。
    finally:
        if previous is None:
            process_file.unlink(missing_ok=True)
        else:
            process_file.write_bytes(previous)
