import asyncio

import httpx
import pytest

from tests.fakes import FakeEchoTool, FakeModelProvider
from yuwang.agent import AgentEngine, AgentStateModel, BudgetExceeded
from yuwang.agent.components import AgentComponents, default_components
from yuwang.agent.engine import AgentDeclaredFailure
from yuwang.domain.models import (
    AgentAction,
    AgentPlan,
    Budget,
    CallStatus,
    EventType,
    MemoryRecord,
    Message,
    MessageRole,
    Observation,
    Run,
    RunStatus,
    TaskSpec,
    Thread,
    ToolCall,
)
from yuwang.model_providers import OpenAICompatibleProvider
from yuwang.policy import PolicyEngine
from yuwang.settings import AgentProfileInput, AgentProfileVersion
from yuwang.storage import SQLiteRepository
from yuwang.tooling import ToolRegistry, ToolSpec


class NonIdempotentFakeEchoTool(FakeEchoTool):
    @property
    def spec(self) -> ToolSpec:
        value = super().spec
        value.idempotent = False
        return value


def build_engine(tmp_path, scenario="success", profile=None, components: AgentComponents | None = None):
    repository = SQLiteRepository(tmp_path / "agent.db")
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    return repository, AgentEngine(
        repository,
        FakeModelProvider(scenario),
        registry,
        PolicyEngine(),
        profile=profile,
        artifact_root=tmp_path / "artifacts",
        components=components,
    )


def profile_for(**overrides):
    value = AgentProfileInput(name="test profile", **overrides)
    return AgentProfileVersion(**value.model_dump(), version=1)


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
    assert len(repository.list_model_calls(run.id)) == 6
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "profile", "expected_status", "expected_level"),
    [
        (
            "advisory",
            profile_for(completion_mode="advisory"),
            "unverified",
            "model",
        ),
        (
            "structured",
            profile_for(
                completion_mode="structured",
                validation_policy={
                    "require_external_evidence": False,
                    "json_schema": {
                        "type": "object",
                        "required": ["title", "priority"],
                        "properties": {
                            "title": {"type": "string"},
                            "priority": {"type": "integer"},
                        },
                    },
                },
            ),
            "validated",
            "structured",
        ),
    ],
)
async def test_pure_model_completion_modes_keep_trust_distinct(
    tmp_path, scenario, profile, expected_status, expected_level
):
    repository, engine = build_engine(tmp_path, scenario, profile)
    thread = repository.save_thread(Thread(title=scenario))
    run = repository.save_run(Run(thread_id=thread.id))
    await engine.run(run.id, TaskSpec(body="generate an answer"))
    finished = repository.get_run(run.id)
    assert finished and finished.status == RunStatus.COMPLETED
    assert finished.validation_status == expected_status
    assert finished.evidence_level == expected_level
    report = repository.get_report(run.id)
    assert report and report[1]["evidence_level"] == expected_level
    if scenario == "advisory":
        assert "未经外部验证" in report[0]


@pytest.mark.asyncio
async def test_waiting_input_resumes_from_checkpoint_without_resetting_budget(tmp_path):
    profile = profile_for(completion_mode="advisory")
    repository, engine = build_engine(tmp_path, "request_input", profile)
    thread = repository.save_thread(Thread(title="waiting"))
    run = repository.save_run(Run(thread_id=thread.id))
    task = TaskSpec(body="ask for missing context")
    await engine.run(run.id, task)
    waiting = repository.get_run(run.id)
    assert waiting and waiting.status == RunStatus.WAITING_INPUT
    checkpoint = repository.latest_checkpoint(run.id)
    assert checkpoint and checkpoint.node == "request_input"
    state = AgentStateModel.model_validate(checkpoint.state)
    consumed_steps = state.step
    state.supplemental_inputs.append("面向技术团队")
    state.action = None
    repository.save_checkpoint(run.id, "input_received", state.model_dump(mode="json"))
    waiting.transition(RunStatus.RUNNING)
    repository.save_run(waiting)
    resumed_engine = AgentEngine(
        repository,
        FakeModelProvider("request_input"),
        engine.registry,
        PolicyEngine(),
        profile=profile,
        artifact_root=tmp_path / "artifacts",
    )
    await resumed_engine.resume(run.id, task)
    finished = repository.get_run(run.id)
    assert finished and finished.status == RunStatus.COMPLETED
    assert repository.latest_checkpoint(run.id).state["step"] > consumed_steps


@pytest.mark.asyncio
async def test_cancelling_inflight_model_request_marks_run_stopped(tmp_path):
    repository, engine = build_engine(tmp_path, "timeout")
    thread = repository.save_thread(Thread(title="cancel"))
    run = repository.save_run(Run(thread_id=thread.id))
    handle = asyncio.create_task(engine.run(run.id, TaskSpec(body="slow request")))
    await asyncio.sleep(0.02)
    handle.cancel()
    await handle
    stopped = repository.get_run(run.id)
    assert stopped and stopped.status == RunStatus.STOPPED


@pytest.mark.asyncio
async def test_competition_request_input_fails_instead_of_fake_waiting(tmp_path):
    profile = profile_for(completion_mode="advisory")
    repository, engine = build_engine(tmp_path, "request_input", profile)
    thread = repository.save_thread(Thread(title="competition", mode="competition"))
    run = repository.save_run(Run(thread_id=thread.id))
    await engine.run(run.id, TaskSpec(body="no input", mode="competition"))
    failed = repository.get_run(run.id)
    assert failed and failed.status == RunStatus.FAILED
    assert failed.status != RunStatus.WAITING_INPUT


@pytest.mark.asyncio
async def test_declarative_direct_workflow_can_omit_planning_nodes(tmp_path):
    profile = profile_for(
        completion_mode="advisory",
        planning_strategy="direct",
        workflow={
            "nodes": ["normalize_task", "select_action", "verify", "generate_report"]
        },
    )
    repository, engine = build_engine(tmp_path, "advisory", profile)
    thread = repository.save_thread(Thread(title="direct"))
    run = repository.save_run(Run(thread_id=thread.id))
    await engine.run(run.id, TaskSpec(body="direct answer"))
    assert repository.get_run(run.id).status == RunStatus.COMPLETED
    assert not any(event.type == EventType.PLAN_UPDATED for event in repository.list_events(run.id))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("strategy", "completion_mode", "expects_plan"),
    [("dynamic", "advisory", True), ("hybrid", "advisory", False), ("hybrid", "evidence", True)],
)
async def test_planning_strategy_has_deterministic_runtime_behavior(
    tmp_path, strategy, completion_mode, expects_plan
):
    profile = profile_for(
        planning_strategy=strategy,
        completion_mode=completion_mode,
        workflow={"preset": "verified"},
    )
    scenario = "advisory" if completion_mode == "advisory" else "success"
    repository, engine = build_engine(tmp_path, scenario, profile)
    thread = repository.save_thread(Thread(title=f"{strategy}-{completion_mode}"))
    run = repository.save_run(Run(thread_id=thread.id))
    task = TaskSpec(
        body="strategy behavior",
        verification_rules=[{"kind": "regex", "value": "verified"}],
    )
    await engine.run(run.id, task)
    has_plan = any(event.type == EventType.PLAN_UPDATED for event in repository.list_events(run.id))
    assert has_plan is expects_plan


def test_context_drift_and_plan_loop_are_rejected(tmp_path):
    repository, engine = build_engine(tmp_path)
    thread = repository.save_thread(Thread(title="guards"))
    run = repository.save_run(Run(thread_id=thread.id))
    state = AgentStateModel(run_id=run.id, task=TaskSpec(body="original"))
    engine._context(state, "first")
    state.task = TaskSpec(body="changed")
    with pytest.raises(AgentDeclaredFailure, match="漂移"):
        engine._context(state, "second")

    state.plan = AgentPlan(summary="same", steps=["one"], success_approach="same")
    engine._track_plan_progress(state)
    engine._track_plan_progress(state)
    with pytest.raises(AgentDeclaredFailure, match="循环规划"):
        engine._track_plan_progress(state)



@pytest.mark.asyncio
async def test_explicit_component_bundle_replaces_planner(tmp_path):
    class SimplePlanner:
        called = False

        async def plan(self, state, invoke):
            self.called = True
            return AgentPlan(summary="custom", steps=["one"], success_approach="safe")

    repository = SQLiteRepository(tmp_path / "components.db")
    components = default_components(repository, tmp_path / "artifacts")
    planner = SimplePlanner()
    components.planner = planner
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    engine = AgentEngine(
        repository,
        FakeModelProvider("advisory"),
        registry,
        PolicyEngine(),
        profile=profile_for(completion_mode="advisory"),
        components=components,
    )
    thread = repository.save_thread(Thread(title="components"))
    run = repository.save_run(Run(thread_id=thread.id))
    await engine.run(run.id, TaskSpec(body="component injection"))
    assert planner.called


@pytest.mark.asyncio
async def test_important_fact_setting_deduplicates_and_enforces_max_facts(tmp_path):
    profile = profile_for(
        planning_strategy="direct",
        completion_mode="advisory",
        workflow={"preset": "direct"},
        memory_policy={"enabled": True, "persist_important_facts": True, "max_facts": 1},
    )
    repository, engine = build_engine(tmp_path, "advisory", profile)
    thread = repository.save_thread(Thread(title="facts"))
    repository.save_memory(MemoryRecord(thread_id=thread.id, kind="important_fact", content="old"))
    run = repository.save_run(Run(thread_id=thread.id))
    await engine.run(run.id, TaskSpec(body="remember preference"))
    memories = repository.list_memories(thread.id, enabled_only=False)
    facts = [item.content for item in memories if item.kind == "important_fact"]
    assert facts == ["用户希望获得中文回答"]
    assert any(item.kind == "run_summary" for item in memories)
    eviction = [event for event in repository.list_events(run.id) if event.type == EventType.WARNING]
    assert any(event.payload.get("reason") == "max_facts" for event in eviction)


@pytest.mark.asyncio
async def test_disabling_important_fact_extraction_skips_extra_model_call(tmp_path):
    profile = profile_for(
        planning_strategy="direct",
        completion_mode="advisory",
        workflow={"preset": "direct"},
        memory_policy={"enabled": True, "persist_important_facts": False, "max_facts": 10},
    )
    repository, engine = build_engine(tmp_path, "advisory", profile)
    thread = repository.save_thread(Thread(title="no facts"))
    run = repository.save_run(Run(thread_id=thread.id))
    await engine.run(run.id, TaskSpec(body="do not remember facts"))
    assert [item.kind for item in repository.list_memories(thread.id)] == ["run_summary"]
    assert len(repository.list_model_calls(run.id)) == 1


@pytest.mark.asyncio
async def test_context_truncation_event_explains_original_and_kept_counts(tmp_path):
    profile = profile_for(
        planning_strategy="direct",
        completion_mode="advisory",
        workflow={"preset": "direct"},
        context_policy={"recent_message_limit": 1},
        memory_policy={"enabled": True, "persist_important_facts": False, "max_facts": 10},
    )
    repository, engine = build_engine(tmp_path, "advisory", profile)
    thread = repository.save_thread(Thread(title="truncation"))
    for content in ["first", "assistant before", "follow-up"]:
        repository.save_message(
            Message(
                thread_id=thread.id,
                role=MessageRole.ASSISTANT if "assistant" in content else MessageRole.USER,
                content=content,
            )
        )
    run = repository.save_run(Run(thread_id=thread.id))
    await engine.run(run.id, TaskSpec(body="follow-up"))
    event = next(
        item for item in repository.list_events(run.id) if item.type == EventType.CONTEXT_TRUNCATED
    )
    assert event.payload["messages"] == {"original": 3, "kept": 1}
    summary = next(
        item for item in repository.list_memories(thread.id) if item.kind == "thread_summary"
    )
    assert "first" in summary.content and "assistant before" in summary.content


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
