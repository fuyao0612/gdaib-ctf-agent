import pytest

from yuwang.dispatch import route_active_message
from yuwang.domain.models import RunStatus


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
