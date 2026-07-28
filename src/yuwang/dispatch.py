"""活动 Run 的确定性控制命令路由。"""

from __future__ import annotations

from typing import Literal

from yuwang.domain.models import RunStatus

ActiveMessageRoute = Literal["stop", "guidance", "input", "clarification"]
_STOP_COMMANDS = {"停止", "停止生成", "停止任务", "取消", "终止", "stop", "cancel"}
_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "！": "!",
        "？": "?",
        "；": ";",
        "：": ":",
        "、": ",",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
    }
)
def _normalize_control_command(content: str) -> str:
    return "".join(content.casefold().split()).translate(_PUNCTUATION_TRANSLATION).strip(
        ".,!?;:"
    )


def route_active_message(content: str, active_status: RunStatus | str) -> ActiveMessageRoute:
    """活动 Run 的控制语义不依赖模型，保证暂停、恢复与取消操作可预测。"""

    status = RunStatus(active_status)
    if _normalize_control_command(content) in _STOP_COMMANDS:
        return "stop"
    if status == RunStatus.WAITING_INPUT:
        return "input"
    if status == RunStatus.WAITING_CLARIFICATION:
        return "clarification"
    return "guidance"
