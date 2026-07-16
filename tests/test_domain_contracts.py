from datetime import UTC
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuwang.domain.models import (
    Artifact,
    Event,
    EventType,
    Message,
    MessageRole,
    Run,
    RunStatus,
    TaskSpec,
    Thread,
)


@pytest.mark.parametrize(
    "model",
    [
        Thread(title="demo"),
        Message(thread_id=uuid4(), role=MessageRole.USER, content="hello"),
        Run(thread_id=uuid4()),
        Event(run_id=uuid4(), sequence=1, type=EventType.RUN_STARTED, summary="start"),
        TaskSpec(body="safe task", authorized_targets=[]),
    ],
)
def test_contract_round_trip(model):
    assert type(model).model_validate_json(model.model_dump_json()) == model
    timestamp = getattr(model, "created_at", getattr(model, "timestamp", None))
    if timestamp:
        assert timestamp.tzinfo == UTC


def test_contracts_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        Thread(title="demo", unexpected=True)


def test_artifact_never_exposes_absolute_path():
    with pytest.raises(ValidationError):
        Artifact(
            thread_id=uuid4(),
            filename="x.txt",
            kind="upload",
            sha256="a" * 64,
            size=1,
            mime_type="text/plain",
            storage_ref="C:\\secret\\x.txt",
        )


def test_run_transitions_reject_illegal_changes():
    run = Run(thread_id=uuid4())
    run.transition(RunStatus.RUNNING)
    run.transition(RunStatus.COMPLETED)
    assert run.started_at and run.finished_at
    with pytest.raises(ValueError, match="illegal"):
        run.transition(RunStatus.RUNNING)


def test_run_control_states_can_resume_without_becoming_terminal():
    for waiting in (
        RunStatus.WAITING_CLARIFICATION,
        RunStatus.WAITING_APPROVAL,
        RunStatus.PAUSED,
    ):
        run = Run(thread_id=uuid4())
        run.transition(RunStatus.RUNNING)
        run.transition(waiting)
        assert run.finished_at is None
        run.transition(RunStatus.RUNNING)
        assert run.status == RunStatus.RUNNING
