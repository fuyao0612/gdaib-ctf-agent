from uuid import uuid4

import pytest

from yuwang.agent import SuccessVerifier
from yuwang.domain.models import EvidenceCandidate, Observation, TaskSpec
from yuwang.storage import SQLiteRepository


def task_with_rule(value: str = r"FLAG\{[A-Z0-9]+\}") -> TaskSpec:
    return TaskSpec(
        body="find flag",
        verification_rules=[{"kind": "regex", "value": value}],
    )


def successful_observation():
    return Observation(
        call_id=uuid4(),
        tool_name="test_tool",
        success=True,
        output={"result": {"candidate": "FLAG{ABC123}"}},
        summary="tool completed",
    )


def test_tool_success_alone_never_means_task_success():
    observation = successful_observation()
    result = SuccessVerifier().verify(task_with_rule(), None, [observation])
    assert not result.verified
    assert "候选" in result.summary


def test_candidate_requires_matching_call_location_and_rule():
    observation = successful_observation()
    verifier = SuccessVerifier()
    accepted = verifier.verify(
        task_with_rule(),
        EvidenceCandidate(
            value="FLAG{ABC123}",
            source_call_id=observation.call_id,
            location="/result/candidate",
        ),
        [observation],
    )
    assert accepted.verified and accepted.rule_kind == "regex"
    wrong_source = verifier.verify(
        task_with_rule(),
        EvidenceCandidate(
            value="FLAG{ABC123}", source_call_id=uuid4(), location="/result/candidate"
        ),
        [observation],
    )
    assert not wrong_source.verified
    wrong_value = verifier.verify(
        task_with_rule(),
        EvidenceCandidate(
            value="FLAG{TAMPERED}",
            source_call_id=observation.call_id,
            location="/result/candidate",
        ),
        [observation],
    )
    assert not wrong_value.verified


def test_sha256_verification():
    import hashlib

    observation = successful_observation()
    value = "FLAG{ABC123}"
    task = TaskSpec(
        body="find flag",
        verification_rules=[
            {"kind": "sha256", "value": hashlib.sha256(value.encode()).hexdigest()}
        ],
    )
    result = SuccessVerifier().verify(
        task,
        EvidenceCandidate(
            value=value,
            source_call_id=observation.call_id,
            location="/result/candidate",
        ),
        [observation],
    )
    assert result.verified


def test_run_task_snapshot_is_immutable(tmp_path):
    repository = SQLiteRepository(tmp_path / "snapshots.db")
    run_id = uuid4()
    original = task_with_rule()
    repository.save_run_task(run_id, original)
    assert repository.get_run_task(run_id) == original
    changed = original.model_copy(update={"body": "new thread message"})
    with pytest.raises(ValueError, match="不可变"):
        repository.save_run_task(run_id, changed)
