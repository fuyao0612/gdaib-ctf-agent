from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
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
        repository.append_event(
            Event(run_id=run.id, sequence=2, type=EventType.WARNING, summary="bad")
        )
    with pytest.raises(ValueError, match="active"):
        repository.save_run(Run(thread_id=thread.id))


def test_active_run_guard_is_atomic_across_repository_instances(tmp_path):
    """不同请求/进程各自持有仓储实例时也不能同时创建活跃 Run。"""

    path = tmp_path / "multi-repository.db"
    thread = SQLiteRepository(path).save_thread(Thread(title="exclusive multi"))
    repositories = [SQLiteRepository(path) for _ in range(4)]
    barrier = Barrier(4)

    def create_active_run(index: int) -> bool:
        repository = repositories[index]
        barrier.wait()
        try:
            repository.save_run(Run(thread_id=thread.id))
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create_active_run, range(4)))

    assert results.count(True) == 1
    assert len(SQLiteRepository(path).list_runs(thread.id)) == 1


def test_missing_records(tmp_path):
    repository = SQLiteRepository(tmp_path / "test.db")
    assert repository.get_thread(uuid4()) is None
    assert repository.get_report(uuid4()) is None
    with pytest.raises(KeyError):
        repository.request_stop(uuid4())


def test_event_sequences_are_transactional_under_concurrency(tmp_path):
    repository = SQLiteRepository(tmp_path / "events.db")
    thread = repository.save_thread(Thread(title="concurrent"))
    run = repository.save_run(Run(thread_id=thread.id))
    events = EventService(repository)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda index: events.emit(run.id, EventType.STATUS_UPDATE, f"event {index}"),
                range(20),
            )
        )
    persisted = repository.list_events(run.id)
    assert [event.sequence for event in persisted] == list(range(1, 21))


def test_checkpoints_are_append_only_versioned_state(tmp_path):
    repository = SQLiteRepository(tmp_path / "checkpoints.db")
    run_id = uuid4()
    repository.save_checkpoint(run_id, "plan", {"value": 1, "elapsed_seconds": 1.5})
    repository.save_checkpoint(run_id, "plan", {"value": 2, "elapsed_seconds": 2.5})
    checkpoints = repository.list_checkpoints(run_id)
    assert [item.checkpoint_sequence for item in checkpoints] == [1, 2]
    assert [item.state["value"] for item in checkpoints] == [1, 2]
    assert checkpoints[-1].state_schema_version == "2.0"
    assert repository.latest_checkpoint(run_id) == checkpoints[-1]
