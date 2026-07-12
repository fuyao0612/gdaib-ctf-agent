
import pytest

from tests.fakes import FakeEchoTool, FakeModelProvider
from yuwang.agent import AgentEngine, AgentStateModel, BudgetExceeded
from yuwang.domain.models import Budget, EventType, Run, RunStatus, TaskSpec, Thread
from yuwang.policy import PolicyEngine
from yuwang.storage import SQLiteRepository
from yuwang.tooling import ToolRegistry


def build_engine(tmp_path, scenario="success"):
    repository = SQLiteRepository(tmp_path / "agent.db")
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    return repository, AgentEngine(
        repository, FakeModelProvider(scenario), registry, PolicyEngine()
    )


@pytest.mark.asyncio
async def test_complete_failure_replan_success_report(tmp_path):
    repository, engine = build_engine(tmp_path)
    thread = repository.save_thread(Thread(title="agent"))
    run = repository.save_run(Run(thread_id=thread.id))
    await engine.run(run.id, TaskSpec(body="执行安全确定性演示"))
    finished = repository.get_run(run.id)
    assert finished.status == RunStatus.COMPLETED
    events = repository.list_events(run.id)
    assert EventType.REPLANNED in [event.type for event in events]
    tool_events = [event for event in events if event.type == EventType.TOOL_FINISHED]
    assert [event.payload["success"] for event in tool_events] == [False, True]
    assert repository.get_report(run.id)[1]["tool_metrics"] == {"calls": 2, "failures": 1}


@pytest.mark.asyncio
async def test_provider_failure_is_safe_and_reported(tmp_path):
    repository, engine = build_engine(tmp_path, "refusal")
    thread = repository.save_thread(Thread(title="failed"))
    run = repository.save_run(Run(thread_id=thread.id))
    await engine.run(run.id, TaskSpec(body="safe task"))
    assert repository.get_run(run.id).status == RunStatus.FAILED
    assert repository.list_events(run.id)[-1].type == EventType.RUN_FAILED
    assert repository.get_report(run.id)


def test_budget_guards_and_stop(tmp_path):
    repository, engine = build_engine(tmp_path)
    thread = repository.save_thread(Thread(title="budget"))
    run = repository.save_run(Run(thread_id=thread.id))
    state = AgentStateModel(run_id=run.id, task=TaskSpec(body="x", budget=Budget(max_steps=1)))
    engine._checkpoint("one", state)
    with pytest.raises(BudgetExceeded):
        engine._checkpoint("two", state)
