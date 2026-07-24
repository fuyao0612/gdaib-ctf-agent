"""OpenAI 兼容 Function Calling 的协议与安全边界测试。"""

from __future__ import annotations

import json

import httpx
import pytest

from tests.fakes import FakeEchoTool
from yuwang.domain.models import ToolSnapshot
from yuwang.model_providers import OpenAICompatibleProvider, ProviderError
from yuwang.tooling.adapters.function_calling import FunctionToolCatalog


def fake_snapshot() -> ToolSnapshot:
    spec = FakeEchoTool().spec
    return ToolSnapshot(
        tool_id=spec.id,
        namespace=spec.namespace,
        name=spec.name,
        display_name=spec.display_name or spec.name,
        version=spec.version,
        source_type=spec.source_type,
        source=spec.source,
        description=spec.description,
        capabilities=spec.capabilities,
        scenarios=spec.scenarios,
        risk=spec.risk,
        permissions=spec.permissions,
        requires_network=spec.requires_network,
        allowed_target_types=spec.allowed_target_types,
        timeout_seconds=spec.timeout_seconds,
        error_codes=spec.error_codes,
        idempotent=spec.idempotent,
        artifact_types=spec.artifact_types,
        input_schema=spec.input_schema,
        output_schema=spec.output_schema,
    )


def test_catalog_converts_schema_and_rejects_unknown_or_invalid_calls() -> None:
    catalog = FunctionToolCatalog.from_snapshots([fake_snapshot()])
    assert catalog.tools[0]["function"]["name"] == "builtin__test_echo"
    assert catalog.tools[0]["function"]["parameters"]["additionalProperties"] is False

    selection = catalog.parse_response(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "function": {
                                    "name": "builtin__test_echo",
                                    "arguments": '{"text":"ok"}',
                                },
                            }
                        ]
                    }
                }
            ]
        }
    )
    assert selection.invocation and selection.invocation.tool_id == "builtin.test_echo"
    assert selection.invocation.arguments == {"text": "ok"}

    with pytest.raises(ValueError, match="未启用"):
        catalog.parse_response(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_456",
                                    "function": {"name": "unknown", "arguments": "{}"},
                                }
                            ]
                        }
                    }
                ]
            }
        )
    with pytest.raises(ValueError, match="工具契约"):
        catalog.parse_response(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_789",
                                    "function": {
                                        "name": "builtin__test_echo",
                                        "arguments": '{"text":"ok","extra":true}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        )


@pytest.mark.asyncio
async def test_native_provider_sends_tools_and_returns_validated_selection() -> None:
    catalog = FunctionToolCatalog.from_snapshots([fake_snapshot()])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tool_choice"] == "auto"
        assert body["tools"] == catalog.tools
        assert "response_format" not in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_native",
                                    "function": {
                                        "name": "builtin__test_echo",
                                        "arguments": '{"text":"native"}',
                                    },
                                }
                            ]
                        }
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    provider = OpenAICompatibleProvider(
        name="native-test",
        base_url="https://provider.test/v1",
        api_key="test-provider-key",
        model="test-model",
        tool_call_mode="native",
        transport=httpx.MockTransport(handler),
    )
    selection = await provider.generate_native_tool_selection("task", catalog)

    assert selection.invocation and selection.invocation.arguments == {"text": "native"}
    assert provider.last_call_metrics and provider.last_call_metrics.total_tokens == 5


@pytest.mark.asyncio
async def test_non_native_provider_refuses_native_call_without_fallback() -> None:
    provider = OpenAICompatibleProvider(
        name="structured-test",
        base_url="https://provider.test/v1",
        api_key="test-provider-key",
        model="test-model",
        tool_call_mode="structured",
    )
    with pytest.raises(ProviderError, match="未启用"):
        await provider.generate_native_tool_selection(
            "task", FunctionToolCatalog.from_snapshots([fake_snapshot()])
        )
