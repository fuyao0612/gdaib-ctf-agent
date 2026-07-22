"""统一消息入口的保守分派规则。

这里刻意不用模型分类：普通聊天不应因为一次误判被强制进入结构化 Agent 流程。
只有用户表达了明确的受控执行意图时才创建 Run，其他内容维持自由文本回复。
"""

from typing import Literal

MessageRoute = Literal["chat", "run", "stop"]

_RUN_MARKERS = (
    "授权 ctf",
    "授权ctf",
    "ctf 题",
    "ctf题",
    "执行任务",
    "运行任务",
    "调用工具",
    "工具调用",
    "验证并报告",
    "验证结果并报告",
)
_STOP_COMMANDS = {"停止", "停止生成", "停止任务", "取消", "终止"}


def route_message(content: str, has_active_run: bool) -> MessageRoute:
    """返回消息应走的内部路径，不把历史 `interaction_mode` 暴露给调用方。"""

    normalized = "".join(content.lower().split()).replace("，", "").replace("。", "")
    if has_active_run and normalized in _STOP_COMMANDS:
        return "stop"
    if any(marker in normalized for marker in _RUN_MARKERS):
        return "run"
    return "chat"
