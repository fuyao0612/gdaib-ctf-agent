from __future__ import annotations

import io
import sys

import pytest

from scripts.check_health_version import main, validate_health


def test_health_version_accepts_matching_healthy_payload() -> None:
    validate_health({"status": "ok", "version": "0.5.0"}, "0.5.0")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"status": "degraded", "version": "0.5.0"}, "状态异常"),
        ({"status": "ok", "version": "0.4.1"}, "版本不一致"),
        ({"status": "ok"}, "版本不一致"),
    ],
)
def test_health_version_rejects_invalid_payload(
    payload: dict[str, str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_health(payload, "0.5.0")


def test_health_version_cli_reads_json_from_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"status":"ok","version":"0.5.0"}'))

    main()

    assert "v0.5.0" in capsys.readouterr().out
