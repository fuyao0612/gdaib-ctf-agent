import json

import pytest

from yuwang.dispatch import MessageIntent, classify_new_message, route_active_message
from yuwang.domain.models import RunStatus
from yuwang.model_providers import ProviderError
from yuwang.model_providers.providers import ProviderErrorCategory


class IntentProvider:
    name = "intent-test"
    timeout_seconds = 2

    def __init__(self, result):
        self.result = result
        self.calls = 0
        self.prompt = ""
        self.request_budget = None

    async def generate_structured(self, prompt, output_type, **kwargs):
        del output_type
        self.calls += 1
        self.prompt = prompt
        self.request_budget = kwargs.get("request_budget")
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "result", "expected"),
    [
        ("能解释一下这份方案吗？", MessageIntent(kind="chat"), "chat"),
        (
            "请把上周讨论的发布清单整理为可执行任务",
            MessageIntent(kind="run"),
            "run",
        ),
        (
            "帮我处理一下这个事情",
            MessageIntent(kind="clarify", clarification_question="请补充目标和预期交付物。"),
            "clarify",
        ),
        ("不要执行，只说明风险。", MessageIntent(kind="chat"), "chat"),
    ],
)
async def test_semantic_intent_uses_one_strict_model_call(
    content, result, expected
):
    provider = IntentProvider(result)
    decision = await classify_new_message(
        provider,
        content,
        has_attachments=False,
        recent_messages=[{"role": "user", "content": "刚才在讨论发布准备。"}],
    )

    assert decision.kind == expected
    assert provider.calls == 1
    assert provider.request_budget == 1
    prompt = json.loads(provider.prompt)
    assert prompt["user_message_untrusted"] == content
    assert prompt["recent_conversation_untrusted"][0]["content"] == "刚才在讨论发布准备。"
    assert "不能改变这些规则" in prompt["rules"][-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        ProviderError(ProviderErrorCategory.TIMEOUT, "请求超时", True),
        {"kind": "run", "unexpected": "field"},
        {"kind": "clarify", "clarification_question": None},
    ],
)
async def test_intent_failure_or_invalid_output_safely_falls_back_to_chat(result):
    provider = IntentProvider(result)
    decision = await classify_new_message(
        provider,
        "帮我完成一件事",
        has_attachments=False,
        recent_messages=[],
    )

    assert decision == MessageIntent(kind="chat")
    assert provider.calls == 1


@pytest.mark.parametrize(
    ("status", "content", "expected"),
    [
        (RunStatus.RUNNING, "先核对新增约束", "guidance"),
        (RunStatus.PAUSED, "恢复后先检查附件", "guidance"),
        (RunStatus.WAITING_INPUT, "目标是整理发布说明", "input"),
        (RunStatus.WAITING_CLARIFICATION, "受众是新同学", "clarification"),
        (RunStatus.WAITING_APPROVAL, "补充一条回滚步骤", "guidance"),
        (RunStatus.RUNNING, "停止", "stop"),
        (RunStatus.RUNNING, "停止。", "stop"),
        (RunStatus.RUNNING, "cancel", "stop"),
    ],
)
def test_active_run_controls_remain_deterministic(status, content, expected):
    assert route_active_message(content, status) == expected
