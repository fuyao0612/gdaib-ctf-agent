import pytest

from tests.fakes import FakeEchoTool, FakeModelProvider
from yuwang.evaluation import (
    EvaluationCase,
    EvaluationCriterion,
    EvaluationRunner,
    builtin_evaluation_cases,
)
from yuwang.settings import ProviderConfig, ProviderPreset
from yuwang.tooling import ToolRegistry


class SecondaryFakeEchoTool(FakeEchoTool):
    @property
    def spec(self):
        return super().spec.model_copy(
            update={
                "id": "test.secondary_echo",
                "namespace": "test",
                "name": "secondary_echo",
            }
        )


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
        allowed_tools=("builtin.test_echo",),
        user_messages=("执行一个可验证的工具任务",),
        expected_outcome="task",
        assertions=(
            "创建 Run",
            "工具快照存在",
            "Agent Profile 快照存在",
            "Provider 快照存在",
            "TOOL_STARTED 已持久化",
            "TOOL_FINISHED 已持久化",
            "生成公开任务说明",
            "快照不含明文 API Key",
        ),
    )

    result = await runner.run_case(case)

    assert result.status == "passed"
    assert result.run_id is not None
    assert {item.status for item in result.assertions} == {"passed"}
    assert runner.repository.list_events(result.run_id)



@pytest.mark.asyncio
async def test_executed_evaluation_with_unmapped_assertion_is_not_provider_unavailable(tmp_path):
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    runner = EvaluationRunner(
        tmp_path / "evaluation.db",
        provider=FakeModelProvider(),
        registry=registry,
        provider_config=ProviderConfig(
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
        ),
        artifact_root=tmp_path / "artifacts",
    )
    result = await runner.run_case(
        EvaluationCase(
            case_id="unmapped-executed-assertion",
            name="未映射断言",
            category="测试",
            allowed_tools=("builtin.test_echo",),
            user_messages=("执行一个可验证的工具任务",),
            expected_outcome="task",
            assertions=("当前未映射的运行语义",),
        )
    )

    assert result.status == "skipped"
    saved = runner.repository.get_evaluation_record(result.record_id)
    assert saved is not None
    assert saved.run_id == result.run_id
    assert saved.failure_category is None


@pytest.mark.asyncio
async def test_required_configuration_error_is_preserved_in_evaluation_record(tmp_path):
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    runner = EvaluationRunner(
        tmp_path / "evaluation.db",
        provider=FakeModelProvider(),
        registry=registry,
        artifact_root=tmp_path / "artifacts",
    )
    case = EvaluationCase(
        case_id="configuration-error",
        name="配置错误保留",
        category="测试",
        version="2.0",
        allowed_tools=("builtin.test_echo",),
        user_messages=("执行一个可验证任务",),
        expected_outcome="task",
        criteria=(
            EvaluationCriterion(
                criterion_id="unsupported",
                description="不支持的验证器",
                validator_type="unknown_validator",
            ),
        ),
        assertions=("创建 Run",),
    )

    result = await runner.run_case(case)

    saved = runner.repository.get_evaluation_record(result.record_id)
    assert result.status == "failed"
    assert saved is not None
    assert saved.failure_category == "configuration_error"
    assert saved.case_version == "2.0"
    assert saved.execution_status == "completed"


@pytest.mark.asyncio
async def test_evaluation_runner_only_snapshots_explicitly_allowed_tools(tmp_path):
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    registry.register(SecondaryFakeEchoTool())
    runner = EvaluationRunner(
        tmp_path / "evaluation.db",
        provider=FakeModelProvider(),
        registry=registry,
        artifact_root=tmp_path / "artifacts",
    )
    case = EvaluationCase(
        case_id="single-allowed-tool",
        name="单工具最小授权",
        category="测试",
        allowed_tools=("builtin.test_echo",),
        user_messages=("执行一个可验证的工具任务",),
        expected_outcome="task",
        assertions=("创建 Run",),
    )

    result = await runner.run_case(case)

    assert result.status == "passed"
    assert result.run_id is not None
    task = runner.repository.get_run_task(result.run_id)
    assert task is not None
    assert [snapshot.tool_id for snapshot in task.tool_snapshots] == ["builtin.test_echo"]


@pytest.mark.asyncio
async def test_evaluation_runner_persists_unknown_allowed_tool_as_configuration_error(tmp_path):
    provider = FakeModelProvider()
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    runner = EvaluationRunner(
        tmp_path / "evaluation.db",
        provider=provider,
        registry=registry,
        artifact_root=tmp_path / "artifacts",
    )
    case = EvaluationCase(
        case_id="unknown-allowed-tool",
        name="未知工具配置失败",
        category="测试",
        allowed_tools=("missing.not_registered",),
        user_messages=("不应启动 Agent",),
        expected_outcome="task",
        assertions=("创建 Run",),
    )

    result = await runner.run_case(case)

    assert result.status == "failed"
    assert result.run_id is None
    assert provider.calls == 0
    assert runner.repository.list_threads() == []
    saved = runner.repository.get_evaluation_record(result.record_id)
    assert saved is not None
    assert saved.failure_category == "configuration_error"
    assert saved.execution_status == "not_executed"
    assert "missing.not_registered" in saved.finish_reason
