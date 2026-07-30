from __future__ import annotations

from uuid import uuid4

from yuwang.domain.models import Run, TaskSpec, VerificationRule
from yuwang.flag_candidates import find_flag_candidates, is_flag_candidate
from yuwang.reports.generator import ReportGenerator


def test_shared_flag_rule_rejects_plain_decode_values_and_accepts_gdaib_prefix() -> None:
    assert not is_flag_candidate("hello")
    assert not is_flag_candidate('{"message":"hello"}')
    assert not is_flag_candidate("https://example.test/flag{not-a-candidate}")
    assert is_flag_candidate("GDAIB{demo}")


def test_custom_regex_extracts_full_matches_instead_of_generic_prefixes() -> None:
    xh = VerificationRule(kind="regex", value=r"XH\{[a-z]+\}")
    cyber = VerificationRule(kind="regex", value=r"CYBER\{([A-Z]+)\}")

    assert is_flag_candidate("XH{demo}", [xh])
    assert find_flag_candidates("answer=XH{demo}", [xh]) == ["XH{demo}"]
    assert find_flag_candidates("CYBER{ONE} CYBER{TWO} CYBER{ONE}", [cyber]) == [
        "CYBER{ONE}", "CYBER{TWO}"
    ]


def test_custom_regex_respects_anchors_bounds_and_invalid_rules() -> None:
    anchored = VerificationRule(kind="regex", value=r"^XH\{[a-z]+\}$")
    unsafe = VerificationRule(kind="regex", value=r"(a+)+$")

    assert find_flag_candidates("XH{demo}", [anchored]) == ["XH{demo}"]
    assert find_flag_candidates("answer=XH{demo}", [anchored]) == []
    assert find_flag_candidates("XH{" + "a" * 2_000 + "}", [VerificationRule(kind="regex", value=r"XH\{a+\}")]) == []
    assert find_flag_candidates("a" * 20_000 + "XH{demo}", [anchored]) == []
    assert find_flag_candidates("XH{demo}", [unsafe]) == []


def test_sha256_only_accepts_explicit_whole_final_answer() -> None:
    import hashlib

    value = "XH{digest}"
    rule = VerificationRule(kind="sha256", value=hashlib.sha256(value.encode()).hexdigest())

    assert find_flag_candidates(f"answer={value}", [rule]) == []
    assert find_flag_candidates(value, [rule]) == []
    assert find_flag_candidates(value, [rule], allow_whole_text_sha256=True) == [value]


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
