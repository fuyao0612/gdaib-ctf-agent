from uuid import uuid4

import pytest

from yuwang.domain.models import Event, EventType, Run, Thread
from yuwang.events import EventService
from yuwang.storage import SQLiteRepository


def test_event_sequence_persistence_and_resume(tmp_path):
    repository = SQLiteRepository(tmp_path / "test.db")
    thread = repository.save_thread(Thread(title="events"))
    run = repository.save_run(Run(thread_id=thread.id))
    events = EventService(repository)
    events.emit(run.id, EventType.RUN_STARTED, "one")
    events.emit(run.id, EventType.STATUS_UPDATE, "two")
    reopened = SQLiteRepository(tmp_path / "test.db")
    assert [event.sequence for event in reopened.list_events(run.id)] == [1, 2]
    assert [event.sequence for event in reopened.list_events(run.id, after=1)] == [2]


def test_rejects_out_of_order_and_concurrent_active_runs(tmp_path):
    repository = SQLiteRepository(tmp_path / "test.db")
    thread = repository.save_thread(Thread(title="exclusive"))
    run = repository.save_run(Run(thread_id=thread.id))
    with pytest.raises(ValueError, match="sequence"):
        repository.append_event(Event(run_id=run.id, sequence=2, type=EventType.WARNING, summary="bad"))
    with pytest.raises(ValueError, match="active"):
        repository.save_run(Run(thread_id=thread.id))


def test_missing_records(tmp_path):
    repository = SQLiteRepository(tmp_path / "test.db")
    assert repository.get_thread(uuid4()) is None
    assert repository.get_report(uuid4()) is None
    with pytest.raises(KeyError):
        repository.request_stop(uuid4())
