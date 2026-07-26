import pytest

from tests.fakes import FakeEchoTool, FakeModelProvider
from yuwang.evaluation import EvaluationCase, EvaluationRunner, builtin_evaluation_cases
from yuwang.model_providers import ProviderCallMetrics
from yuwang.settings import ProviderConfig, ProviderPreset
from yuwang.tooling import ToolRegistry


class ChatMetricsProvider(FakeModelProvider):
    """隔离的聊天协议替身，只用于验证正式评测持久化路径。"""

    tool_call_mode = "structured"
    model = "chat-test-model"

    def __init__(self) -> None:
        super().__init__()
        self.last_call_metrics: ProviderCallMetrics | None = None
        self.prompts: list[list[dict[str, str]]] = []

    async def generate_text(self, messages, *, system_prompt, timeout=None):
        del system_prompt, timeout
        self.calls += 1
        self.prompts.append(messages)
        self.last_call_metrics = ProviderCallMetrics(
            provider=self.name,
            model=self.model,
            request_count=1,
            retry_count=0,
            duration_ms=12,
            input_tokens=9,
            output_tokens=4,
            total_tokens=13,
            cost=0.003,
            usage_reported=True,
        )
        return "这是持久化的聊天回复。"


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
@pytest.mark.skip(reason="评测普通聊天路径已由 Agent Run 统一替代")
async def test_evaluation_runner_executes_chat_and_persists_provider_metrics(tmp_path):
    provider = ChatMetricsProvider()
    config = ProviderConfig(
        name="聊天评测配置",
        preset=ProviderPreset.CUSTOM,
        base_url="https://provider.example/v1",
        model="chat-test-model",
        encrypted_api_key="test-encrypted-value",
        enabled=True,
        is_default=True,
        fallback_order=0,
        timeout_seconds=30,
        max_retries=0,
    )
    runner = EvaluationRunner(
        tmp_path / "evaluation.db",
        provider=provider,
        provider_config=config,
    )
    case = EvaluationCase(
        case_id="runner-chat-path",
        name="普通聊天执行路径",
        category="测试",
        user_messages=("你好",),
        expected_outcome="chat",
        assertions=("不创建 Run", "返回自然语言回复", "不宣称外部验证"),
    )

    result = await runner.run_case(case)

    assert result.status == "passed"
    assert result.run_id is None and result.thread_id is not None
    thread = runner.repository.get_thread(result.thread_id)
    assert thread is not None
    assert thread.interaction_mode == "chat"
    assert thread.provider_config_id == config.id
    assert [message.role for message in runner.repository.list_messages(result.thread_id)] == [
        "user",
        "assistant",
    ]
    assert provider.prompts == [[{"role": "user", "content": "你好"}]]
    record = runner.repository.get_evaluation_record(result.record_id)
    assert record is not None
    assert (record.model_calls, record.input_tokens, record.output_tokens) == (1, 9, 4)
    assert record.estimated_cost == 0.003
    assert record.trace_path == f"/api/v1/threads/{result.thread_id}"


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
