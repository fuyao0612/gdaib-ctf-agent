import httpx
import pytest

from tests.fakes import FakeEchoTool, FakeModelProvider
from yuwang.domain.models import AgentAction, TaskSpec
from yuwang.model_providers import OpenAICompatibleProvider, ProviderChain, ProviderError
from yuwang.policy import PolicyEngine, redact
from yuwang.tooling.sdk import ToolExecutor, ToolRegistry


def test_policy_default_deny_and_local_allow():
    policy = PolicyEngine()
    task = TaskSpec(body="probe", authorized_targets=["localhost"])
    assert not policy.check_tool(task, "missing", {}, {"test_echo"}).allowed
    assert not policy.check_tool(
        task,
        "localhost_http_probe",
        {"url": "https://example.com"},
        {"localhost_http_probe"},
    ).allowed
    assert policy.check_tool(
        task,
        "localhost_http_probe",
        {"url": "http://localhost:8000"},
        {"localhost_http_probe"},
    ).allowed


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
                    {
                        "message": {
                            "content": '{"kind":"finish","summary":"done","tool_input":{}}'
                        }
                    }
                ]
            },
        )

    provider = provider_with_transport(httpx.MockTransport(handler))
    action = await provider.generate_structured("task", AgentAction)
    assert action.kind == "finish"


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
async def test_provider_chain_falls_back_without_agent_changes():
    chain = ProviderChain([FakeModelProvider("refusal"), FakeModelProvider("success")])
    action = await chain.generate_structured("tool_failures=1", AgentAction, timeout=1)
    assert action.tool_name == "test_echo"


def test_provider_rejects_empty_key():
    with pytest.raises(ValueError, match="API Key"):
        OpenAICompatibleProvider(
            name="bad", base_url="https://provider.test", api_key="", model="x"
        )
