import pytest

from yuwang.dispatch import MessageIntent, classify_new_message, route_active_message
from yuwang.domain.models import RunStatus


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("能解释一下这份方案吗？", "chat"),
        ("请把上周讨论的发布清单整理为可执行任务", "run"),
        ("帮我处理一下这个事情", "clarify"),
        ("不要执行，只说明风险。", "chat"),
        ("请使用 encoding_decode 工具解码 SGVsbG8=", "run"),
    ],
)
async def test_new_message_dispatch_is_deterministic_and_does_not_call_a_model(content, expected):
    decision = await classify_new_message(
        content,
        has_attachments=False,
        recent_messages=[{"role": "user", "content": "刚才在讨论发布准备。"}],
    )

    assert decision.kind == expected


@pytest.mark.asyncio
async def test_attachment_starts_a_controlled_task_without_model_routing():
    decision = await classify_new_message(
        "请处理这个附件", has_attachments=True, recent_messages=[]
    )

    assert decision == MessageIntent(kind="run")


@pytest.mark.asyncio
async def test_explicit_continuation_of_a_prior_plan_starts_a_run():
    decision = await classify_new_message(
        "继续刚才那个安排。",
        has_attachments=False,
        recent_messages=[{"role": "user", "content": "我想准备发布说明。"}],
    )

    assert decision == MessageIntent(kind="run")


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
