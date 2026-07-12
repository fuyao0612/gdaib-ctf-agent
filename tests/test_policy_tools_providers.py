
import pytest

from yuwang.domain.models import AgentAction, TaskSpec
from yuwang.model_providers import (
    MockModelProvider,
    OpenAICompatibleProvider,
    ProviderChain,
    ProviderError,
)
from yuwang.policy import PolicyEngine, redact
from yuwang.tooling.sdk import MockEchoTool, ToolExecutor, ToolRegistry


def test_policy_default_deny_and_local_allow():
    policy = PolicyEngine()
    task = TaskSpec(body="probe", authorized_targets=["localhost"])
    assert not policy.check_tool(task, "missing", {}, {"mock_echo"}).allowed
    assert not policy.check_tool(task, "localhost_http_probe", {"url": "https://example.com"}, {"localhost_http_probe"}).allowed
    assert policy.check_tool(task, "localhost_http_probe", {"url": "http://localhost:8000"}, {"localhost_http_probe"}).allowed


def test_redaction():
    value = redact("api_key=topsecret token:abc123 sk-1234567890ABCDE")
    assert "topsecret" not in value and "abc123" not in value and "sk-" not in value


@pytest.mark.asyncio
async def test_tool_registry_validation_and_failure_isolation():
    registry = ToolRegistry()
    registry.register(MockEchoTool())
    with pytest.raises(ValueError):
        registry.register(MockEchoTool())
    executor = ToolExecutor(registry)
    assert (await executor.execute("mock_echo", {"text": "ok"})).success
    assert (await executor.execute("mock_echo", {"text": "ok", "fail": True})).error.code == "execution_error"
    assert (await executor.execute("mock_echo", {})).error.code == "invalid_input"
    assert (await executor.execute("unknown", {})).error.code == "execution_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,category", [("refusal", "refusal"), ("invalid", "invalid_output")])
async def test_mock_provider_errors(scenario, category):
    with pytest.raises(ProviderError) as error:
        await MockModelProvider(scenario).generate_structured("x", AgentAction, timeout=0.1)
    assert error.value.category == category


@pytest.mark.asyncio
async def test_mock_provider_timeout_and_retry_success():
    with pytest.raises(ProviderError):
        await MockModelProvider("timeout").generate_structured("x", AgentAction, timeout=0.001)
    provider = MockModelProvider("fail_then_success")
    with pytest.raises(ProviderError):
        await provider.generate_structured("x", AgentAction, timeout=1)
    action = await provider.generate_structured("tool_failures=1", AgentAction, timeout=1)
    assert action.kind == "call_tool"


@pytest.mark.asyncio
async def test_unconfigured_compatible_provider():
    provider = OpenAICompatibleProvider(base_url="", api_key="", model="")
    assert not provider.configured
    with pytest.raises(ProviderError):
        await provider.generate_structured("x", AgentAction, timeout=1)


@pytest.mark.asyncio
async def test_provider_chain_falls_back_without_agent_changes():
    chain = ProviderChain([MockModelProvider("refusal"), MockModelProvider("success")])
    action = await chain.generate_structured("tool_failures=1", AgentAction, timeout=1)
    assert action.tool_name == "mock_echo"
