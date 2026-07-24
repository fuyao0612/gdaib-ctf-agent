import httpx
import pytest

from tests.fakes import FakeEchoTool, FakeModelProvider
from yuwang.domain.models import AgentAction, TaskSpec
from yuwang.model_providers import OpenAICompatibleProvider, ProviderChain, ProviderError
from yuwang.policy import PolicyEngine, redact
from yuwang.tooling.sdk import LocalhostHTTPProbeTool, ToolExecutor, ToolRegistry


def test_policy_default_deny_and_local_allow():
    policy = PolicyEngine()
    task = TaskSpec(body="probe", authorized_targets=["localhost"])
    assert policy.check_tool(task, FakeEchoTool().spec, {}).allowed
    probe = LocalhostHTTPProbeTool().spec
    assert not policy.check_tool(
        task,
        probe,
        {"url": "https://example.com"},
    ).allowed
    assert policy.check_tool(
        task,
        probe,
        {"url": "http://localhost:8000"},
    ).allowed


def test_high_risk_tool_is_rejected_before_execution():
    policy = PolicyEngine()
    high_risk = FakeEchoTool().spec.model_copy(update={"risk": "high"})
    decision = policy.check_tool(TaskSpec(body="只读任务"), high_risk, {})

    assert not decision.allowed
    assert not decision.requires_approval
    assert "高风险" in decision.reason


def test_redaction():
    value = redact("api_key=topsecret token:abc123 sk-1234567890ABCDE")
    assert "topsecret" not in value and "abc123" not in value and "sk-" not in value


@pytest.mark.asyncio
async def test_tool_registry_validation_and_failure_isolation():
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    with pytest.raises(ValueError):
        registry.register(FakeEchoTool())
    executor = ToolExecutor(registry)
    assert (await executor.execute("test_echo", {"text": "ok"})).success
    failed = await executor.execute("test_echo", {"text": "ok", "fail": True})
    assert failed.error and failed.error.code == "execution_error"
    invalid = await executor.execute("test_echo", {})
    assert invalid.error and invalid.error.code == "invalid_input"
    unknown = await executor.execute("unknown", {})
    assert unknown.error and unknown.error.code == "execution_error"


def provider_with_transport(
    transport: httpx.AsyncBaseTransport, *, retries: int = 0, mode: str = "json_schema"
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="协议测试",
        base_url="https://provider.test/v1",
        api_key="test-secret-key",
        model="test-model",
        max_retries=retries,
        structured_mode=mode,
        input_price_per_million=2,
        output_price_per_million=4,
        transport=transport,
    )


@pytest.mark.asyncio
async def test_compatible_provider_validates_schema_and_never_sends_key_in_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://provider.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret-key"
        body = __import__("json").loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"kind":"finish","summary":"done","tool_input":{}}'}}
                ],
                "usage": {"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22},
            },
        )

    provider = provider_with_transport(httpx.MockTransport(handler))
    action = await provider.generate_structured("task", AgentAction)
    assert action.kind == "finish"
    assert provider.last_call_metrics
    assert provider.last_call_metrics.total_tokens == 22
    assert provider.last_call_metrics.cost == pytest.approx(0.000054)
    assert provider.last_call_metrics.usage_reported


@pytest.mark.asyncio
async def test_free_text_provider_never_forces_json_and_reads_standard_stream():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        requests.append(body)
        assert "response_format" not in body
        if body["stream"]:
            stream = (
                'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"好"}}],'
                '"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(
                200,
                content=stream.encode(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "自然语言回答"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            },
        )

    provider = provider_with_transport(httpx.MockTransport(handler))
    messages = [{"role": "user", "content": "你好"}]
    assert await provider.generate_text(messages, system_prompt="直接回答") == "自然语言回答"
    chunks = [
        chunk
        async for chunk in provider.stream_text(messages, system_prompt="直接回答")
    ]
    assert "".join(chunks) == "你好"
    assert requests[0]["stream"] is False
    assert requests[1]["stream"] is True
    assert provider.last_call_metrics and provider.last_call_metrics.total_tokens == 3


@pytest.mark.asyncio
async def test_free_text_stream_accepts_usage_only_terminal_event():
    """兼容部分服务在 [DONE] 前发送的 choices 为空的用量尾包。"""

    def handler(_: httpx.Request) -> httpx.Response:
        stream = (
            'data: {"choices":[{"delta":{"content":"回复完成"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":2,'
            '"completion_tokens":2,"total_tokens":4}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            content=stream.encode(),
            headers={"content-type": "text/event-stream"},
        )

    provider = provider_with_transport(httpx.MockTransport(handler))
    chunks = [
        chunk
        async for chunk in provider.stream_text(
            [{"role": "user", "content": "你好"}], system_prompt="直接回答"
        )
    ]

    assert "".join(chunks) == "回复完成"
    assert provider.last_call_metrics and provider.last_call_metrics.total_tokens == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,category,retryable",
    [(401, "auth", False), (403, "auth", False), (400, "service", False)],
)
async def test_provider_non_retryable_errors(status, category, retryable):
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    with pytest.raises(ProviderError) as caught:
        await provider_with_transport(httpx.MockTransport(handler), retries=3).generate_structured(
            "task", AgentAction
        )
    assert caught.value.category == category
    assert caught.value.retryable is retryable
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,category", [(429, "rate_limit"), (503, "service")])
async def test_provider_retries_only_transient_statuses(status, category):
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    with pytest.raises(ProviderError) as caught:
        await provider_with_transport(httpx.MockTransport(handler), retries=2).generate_structured(
            "task", AgentAction
        )
    assert caught.value.category == category
    assert calls == 3


@pytest.mark.asyncio
async def test_provider_request_budget_caps_configured_retries():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    provider = provider_with_transport(httpx.MockTransport(handler), retries=8)
    with pytest.raises(ProviderError) as caught:
        await provider.generate_structured("task", AgentAction, request_budget=2)
    assert calls == 2
    assert caught.value.metrics
    assert caught.value.metrics.request_count == 2
    assert caught.value.metrics.retry_count == 1


@pytest.mark.asyncio
async def test_provider_invalid_output_and_refusal_are_classified():
    responses = iter(
        [
            httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]}),
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": "{}", "refusal": "no"}}]},
            ),
        ]
    )
    provider = provider_with_transport(httpx.MockTransport(lambda _: next(responses)))
    with pytest.raises(ProviderError) as invalid:
        await provider.generate_structured("task", AgentAction)
    assert invalid.value.category == "invalid_output"
    with pytest.raises(ProviderError) as refusal:
        await provider.generate_structured("task", AgentAction)
    assert refusal.value.category == "refusal"


@pytest.mark.asyncio
async def test_provider_chain_falls_back_only_for_configured_categories():
    chain = ProviderChain(
        [FakeModelProvider("service"), FakeModelProvider("success")], retry_budget=1
    )
    action = await chain.generate_structured('{"observations":[]}', AgentAction, timeout=1)
    assert action.tool_name == "test_echo"
    refusing = ProviderChain([FakeModelProvider("refusal"), FakeModelProvider("success")])
    with pytest.raises(ProviderError, match="refusal"):
        await refusing.generate_structured('{"observations":[]}', AgentAction, timeout=1)


def test_provider_rejects_empty_key():
    with pytest.raises(ValueError, match="API Key"):
        OpenAICompatibleProvider(
            name="bad", base_url="https://provider.test", api_key="", model="x"
        )


@pytest.mark.asyncio
async def test_prompt_compatibility_mode_and_model_discovery():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "model-b"}, {"id": "model-a"}]})
        body = __import__("json").loads(request.content)
        assert "response_format" not in body
        assert "JSON Schema" in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"kind":"finish","summary":"ok"}'}}],
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            },
        )

    provider = provider_with_transport(httpx.MockTransport(handler), mode="prompt_json")
    assert (await provider.generate_structured("task", AgentAction)).kind == "finish"
    assert await provider.discover_models() == ["model-a", "model-b"]
    assert [request.method for request in requests] == ["POST", "GET"]
