import pytest

from tests.fakes import FakeEchoTool, FakeModelProvider
from yuwang.evaluation import EvaluationCase, EvaluationRunner, builtin_evaluation_cases
from yuwang.settings import ProviderConfig, ProviderPreset
from yuwang.tooling import ToolRegistry


@pytest.mark.asyncio
async def test_evaluation_runner_skips_when_no_real_provider_is_injected(tmp_path):
    runner = EvaluationRunner(tmp_path / "evaluation.db")
    cases = builtin_evaluation_cases()

    results = await runner.run(cases)

    assert len(results) == len(cases)
    assert {result.status for result in results} == {"skipped"}
    assert all("未显式注入" in (result.reason or "") for result in results)


@pytest.mark.asyncio
async def test_evaluation_runner_uses_agent_events_and_tool_snapshots(tmp_path):
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    provider_config = ProviderConfig(
        name="评测配置快照",
        preset=ProviderPreset.CUSTOM,
        base_url="https://provider.example/v1",
        model="test-model",
        encrypted_api_key="test-encrypted-value",
        enabled=True,
        is_default=True,
        fallback_order=0,
        timeout_seconds=30,
        max_retries=0,
    )
    runner = EvaluationRunner(
        tmp_path / "evaluation.db",
        provider=FakeModelProvider(),  # 仅测试替身；生产运行器从不内置它。
        registry=registry,
        provider_config=provider_config,
        artifact_root=tmp_path / "artifacts",
    )
    case = EvaluationCase(
        case_id="runner-agent-path",
        name="Agent 执行路径",
        category="测试",
        user_messages=("执行一个可验证的工具任务",),
        expected_outcome="task",
        assertions=(
            "创建 Run",
            "工具快照存在",
            "Agent Profile 快照存在",
            "Provider 快照存在",
            "TOOL_STARTED 已持久化",
            "TOOL_FINISHED 已持久化",
        ),
    )

    result = await runner.run_case(case)

    assert result.status == "passed"
    assert result.run_id is not None
    assert {item.status for item in result.assertions} == {"passed"}
    assert runner.repository.list_events(result.run_id)
