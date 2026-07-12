import httpx
import pytest

from tests.fakes import FakeEchoTool, FakeModelProvider
from yuwang.agent import AgentEngine, AgentStateModel, BudgetExceeded
from yuwang.domain.models import (
    AgentAction,
    AgentPlan,
    Budget,
    CallStatus,
    EventType,
    Observation,
    Run,
    RunStatus,
    TaskSpec,
    Thread,
    ToolCall,
)
from yuwang.model_providers import OpenAICompatibleProvider
from yuwang.policy import PolicyEngine
from yuwang.storage import SQLiteRepository
from yuwang.tooling import ToolRegistry, ToolSpec


class NonIdempotentFakeEchoTool(FakeEchoTool):
    @property
    def spec(self) -> ToolSpec:
        value = super().spec
        value.idempotent = False
        return value


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
    await engine.run(
        run.id,
        TaskSpec(
            body="执行安全测试任务",
            verification_rules=[{"kind": "regex", "value": "verified"}],
        ),
    )
    finished = repository.get_run(run.id)
    assert finished.status == RunStatus.COMPLETED
    events = repository.list_events(run.id)
    assert EventType.REPLANNED in [event.type for event in events]
    tool_events = [event for event in events if event.type == EventType.TOOL_FINISHED]
    assert [event.payload["success"] for event in tool_events] == [False, True]
    assert repository.get_report(run.id)[1]["tool_metrics"] == {"calls": 2, "failures": 1}
    assert len(repository.list_model_calls(run.id)) == 5
    assert [call.status for call in repository.list_tool_calls(run.id)] == [
        CallStatus.FAILED,
        CallStatus.SUCCEEDED,
    ]
    assert repository.list_evidence(run.id)[0].verified
    checkpoints = repository.list_checkpoints(run.id)
    assert [item.checkpoint_sequence for item in checkpoints] == list(
        range(1, len(checkpoints) + 1)
    )


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


@pytest.mark.parametrize(
    ("budget", "state_update"),
    [
        (Budget(max_model_calls=1), {"model_calls": 2}),
        (Budget(max_tool_calls=1), {"tool_calls": 2}),
        (Budget(max_tokens=10), {"tokens": 11}),
        (Budget(max_model_cost=0.01), {"model_cost": 0.02}),
        (Budget(max_duration_seconds=1), {"elapsed_seconds": 2}),
    ],
)
def test_each_budget_dimension_is_enforced(tmp_path, budget, state_update):
    repository, engine = build_engine(tmp_path)
    thread = repository.save_thread(Thread(title="budget dimensions"))
    run = repository.save_run(Run(thread_id=thread.id))
    state = AgentStateModel(
        run_id=run.id,
        task=TaskSpec(body="x", budget=budget),
        **state_update,
    )
    with pytest.raises(BudgetExceeded):
        engine._checkpoint("budget", state)


@pytest.mark.asyncio
async def test_engine_accounts_provider_reported_requests_and_tokens(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"summary":"plan","steps":["one"],"success_approach":"verify"}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 31, "completion_tokens": 7, "total_tokens": 38},
            },
        )

    repository = SQLiteRepository(tmp_path / "metering.db")
    thread = repository.save_thread(Thread(title="metering"))
    run = repository.save_run(Run(thread_id=thread.id))
    provider = OpenAICompatibleProvider(
        name="metered",
        base_url="https://provider.test/v1",
        api_key="test-provider-key",
        model="metered-model",
        input_price_per_million=2,
        output_price_per_million=4,
        structured_mode="json_object",
        transport=httpx.MockTransport(handler),
    )
    engine = AgentEngine(repository, provider, ToolRegistry(), PolicyEngine())
    state = AgentStateModel(run_id=run.id, task=TaskSpec(body="meter this call"))
    result = await engine._model_call(state, AgentPlan, "metering test")
    assert isinstance(result, AgentPlan)
    assert state.model_calls == 1 and state.tokens == 38
    assert state.model_cost == pytest.approx(0.00009)
    recorded = repository.list_model_calls(run.id)[0]
    assert (recorded.input_tokens, recorded.output_tokens) == (31, 7)
    assert recorded.metadata["usage_reported"] is True
    assert recorded.metadata["cost"] == pytest.approx(0.00009)


@pytest.mark.asyncio
async def test_resume_from_last_safe_checkpoint_without_replaying_completed_tool(tmp_path):
    repository, engine = build_engine(tmp_path)
    thread = repository.save_thread(Thread(title="resume"))
    run = Run(thread_id=thread.id, provider="test-provider")
    run.transition(RunStatus.RUNNING)
    repository.save_run(run)
    task = TaskSpec(
        body="resume task",
        verification_rules=[{"kind": "regex", "value": "verified"}],
    )
    repository.save_run_task(run.id, task)
    observation = Observation(
        call_id=__import__("uuid").uuid4(),
        tool_name="test_echo",
        success=True,
        output={"echoed": "verified"},
        summary="already completed before restart",
    )
    state = AgentStateModel(
        run_id=run.id,
        task=task,
        step=6,
        model_calls=2,
        tool_calls=1,
        plan=AgentPlan(
            summary="existing plan",
            steps=["use existing observation"],
            success_approach="verify evidence",
        ),
        action=AgentAction(
            kind="call_tool",
            summary="completed action",
            tool_name="test_echo",
            tool_input={"text": "verified"},
        ),
        observations=[observation],
        elapsed_seconds=3.5,
    )
    repository.save_checkpoint(run.id, "execute_tool", state.model_dump(mode="json"))
    await engine.resume(run.id, task)
    finished = repository.get_run(run.id)
    assert finished and finished.status == RunStatus.COMPLETED
    assert len(repository.list_tool_calls(run.id)) == 0
    assert repository.list_evidence(run.id)[0].source_call_id == observation.call_id
    resumed = [event for event in repository.list_events(run.id) if "恢复" in event.summary]
    assert resumed and resumed[0].payload["resume_node"] == "observe"


@pytest.mark.asyncio
async def test_resume_refuses_uncertain_non_idempotent_tool(tmp_path):
    repository = SQLiteRepository(tmp_path / "unsafe.db")
    registry = ToolRegistry()
    registry.register(NonIdempotentFakeEchoTool())
    engine = AgentEngine(repository, FakeModelProvider(), registry, PolicyEngine())
    thread = repository.save_thread(Thread(title="unsafe resume"))
    run = Run(thread_id=thread.id, provider="test-provider")
    run.transition(RunStatus.RUNNING)
    repository.save_run(run)
    task = TaskSpec(body="unsafe")
    state = AgentStateModel(
        run_id=run.id,
        task=task,
        action=AgentAction(
            kind="call_tool", summary="side effect", tool_name="test_echo", tool_input={"text": "x"}
        ),
    )
    repository.save_checkpoint(run.id, "policy_check", state.model_dump(mode="json"))
    call_id = __import__("uuid").uuid4()
    repository.save_tool_call(
        ToolCall(
            id=call_id,
            run_id=run.id,
            tool_name="test_echo",
            input_summary="side effect",
            duration_ms=0,
            status=CallStatus.STARTED,
        )
    )
    await engine.resume(run.id, task)
    failed = repository.get_run(run.id)
    assert failed and failed.status == RunStatus.FAILED
    assert "禁止自动重复" in (failed.error or "")
