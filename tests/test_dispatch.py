import pytest

from yuwang.dispatch import route_message
from yuwang.domain.models import RunStatus


@pytest.mark.parametrize(
    ("content", "attachments", "expected"),
    [
        ("你好", False, "chat"),
        ("解释一下 SQL 注入原理，不要执行", False, "chat"),
        ("请完成这道 Web 题并找到 flag", False, "run"),
        ("Analyze this file and give the final answer", True, "run"),
        ("分析这个文件并给出结果", True, "run"),
        ("帮我看看这个思路", False, "chat"),
    ],
)
def test_route_message_handles_chinese_english_attachment_and_negation(
    content: str, attachments: bool, expected: str
) -> None:
    assert route_message(content, has_attachments=attachments) == expected


@pytest.mark.parametrize(
    ("status", "content", "expected"),
    [
        (RunStatus.RUNNING, "先核对新增约束", "guidance"),
        (RunStatus.PAUSED, "恢复后先检查附件", "guidance"),
        (RunStatus.WAITING_INPUT, "目标是本地靶场", "input"),
        (RunStatus.WAITING_CLARIFICATION, "受众是新同学", "clarification"),
        (RunStatus.WAITING_APPROVAL, "补充一个回滚步骤", "guidance"),
        (RunStatus.RUNNING, "停止", "stop"),
        (RunStatus.RUNNING, "停止。", "stop"),
        (RunStatus.RUNNING, "cancel", "stop"),
    ],
)
def test_active_run_always_interprets_the_same_input_as_run_control(
    status: RunStatus, content: str, expected: str
) -> None:
    assert route_message(content, status) == expected
