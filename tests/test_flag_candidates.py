from __future__ import annotations

from uuid import uuid4

from yuwang.domain.models import Run, TaskSpec
from yuwang.flag_candidates import is_flag_candidate
from yuwang.reports.generator import ReportGenerator


def test_shared_flag_rule_rejects_plain_decode_values_and_accepts_gdaib_prefix() -> None:
    assert not is_flag_candidate("hello")
    assert not is_flag_candidate('{"message":"hello"}')
    assert not is_flag_candidate("https://example.test/flag{not-a-candidate}")
    assert is_flag_candidate("GDAIB{demo}")


def test_report_does_not_promote_plain_decode_value_to_candidate_or_evidence() -> None:
    call_id = str(uuid4())
    _, data = ReportGenerator().generate(
        Run(thread_id=uuid4()),
        TaskSpec(body="解码公开文本", scenario="ctf"),
        [],
        {
            "validation_status": "unverified",
            "trace": {
                "steps": [{
                    "sequence": 1, "call_id": call_id, "tool_id": "ctf.encoding_decode",
                    "observation_summary": "解码得到 hello", "preview": "hello",
                }],
                "tool_calls": [{"id": call_id, "result_summary": "hello"}],
                "evidence": [{
                    "candidate": "hello", "source_call_id": call_id,
                    "discovery_source": "encoding_decode", "verified": False,
                }],
                "metrics": {}, "artifacts": [],
            },
        },
    )

    assert data["flag_candidates"] == []
    assert data["evidence_level"] != "external"


def test_report_backfills_legacy_evidence_source_step_from_call_id() -> None:
    call_id = str(uuid4())
    _, data = ReportGenerator().generate(
        Run(thread_id=uuid4()),
        TaskSpec(body="读取 CTF", scenario="ctf"),
        [],
        {
            "validation_status": "unverified",
            "trace": {
                "steps": [{
                    "sequence": 7, "call_id": call_id, "tool_id": "ctf.encoding_decode",
                    "observation_summary": "解码完成", "preview": "GDAIB{demo}",
                }],
                "tool_calls": [],
                "evidence": [{
                    "candidate": "GDAIB{demo}", "source_call_id": call_id,
                    "discovery_source": "encoding_decode", "verified": False,
                }],
                "metrics": {}, "artifacts": [],
            },
        },
    )

    assert data["flag_candidates"][0]["source_step"] == 7
