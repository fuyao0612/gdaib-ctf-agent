"""统一消息入口的保守、可测试分派规则。

规则先处理用户明确否定，再识别当前 Run 的生命周期；只有没有活动 Run 时才判断
是否需要受控执行。无法确定的表达安全降级为普通聊天，避免把一句解释性问题强制
变成执行任务。后续可在此模块注入轻量模型分类器，但不能影响消息持久化顺序。
"""

from __future__ import annotations

from typing import Literal

from yuwang.domain.models import RunStatus

MessageRoute = Literal[
    "chat",
    "run",
    "stop",
    "guidance",
    "input",
    "clarification",
]

_STOP_COMMANDS = {"停止", "停止生成", "停止任务", "取消", "终止", "stop", "cancel"}
_NEGATED_EXECUTION = (
    "只解释",
    "不要执行",
    "无需执行",
    "不需要执行",
    "不要运行",
    "只要说明",
    "explain only",
    "do not execute",
    "don't execute",
    "do not run",
)
_EXECUTION_MARKERS = (
    "完成这道",
    "完成题目",
    "完成任务",
    "拿到flag",
    "拿到 flag",
    "找到flag",
    "找到 flag",
    "找出最终答案",
    "给出验证结果",
    "验证并报告",
    "验证结果并报告",
    "执行任务",
    "运行任务",
    "调用工具",
    "工具调用",
    "检查本地",
    "分析这个文件",
    "分析该文件",
    "分析附件",
    "完成这道web题",
    "solve this",
    "complete this",
    "find the flag",
    "verify and report",
    "execute the task",
    "run the task",
    "analyze this file",
    "analyse this file",
    "inspect the local",
)
_ATTACHMENT_ACTION_MARKERS = (
    "分析",
    "检查",
    "提取",
    "找出",
    "验证",
    "analyze",
    "analyse",
    "inspect",
    "extract",
    "verify",
)

# 全角标点不会改变意图，但若不统一，像“停止。”这样的自然输入会错过
# 精确的停止命令。使用字典而不是两个等长字符串，避免新增标点时引入隐蔽错误。
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


def _normalize(content: str) -> str:
    return "".join(content.casefold().split()).translate(_PUNCTUATION_TRANSLATION).strip(
        ".,!?;:"
    )


def _has_negated_execution(value: str) -> bool:
    return any(marker.replace(" ", "") in value for marker in _NEGATED_EXECUTION)


def route_message(
    content: str,
    active_status: RunStatus | str | None = None,
    *,
    has_attachments: bool = False,
) -> MessageRoute:
    """返回当前消息的内部去向，不向调用方暴露旧 `interaction_mode`。

    有活动 Run 时，文本默认是对当前任务的补充或纠偏，而不是尝试并行创建第二个
    Run。停止短语优先级最高；等待补充/澄清则在同一输入框直接恢复检查点。
    """

    normalized = _normalize(content)
    status = RunStatus(active_status) if active_status is not None else None
    if status and normalized in _STOP_COMMANDS:
        return "stop"
    if status == RunStatus.WAITING_INPUT:
        return "input"
    if status == RunStatus.WAITING_CLARIFICATION:
        return "clarification"
    if status:
        return "guidance"
    if _has_negated_execution(normalized):
        return "chat"
    if any(marker.replace(" ", "") in normalized for marker in _EXECUTION_MARKERS):
        return "run"
    if has_attachments and any(marker in normalized for marker in _ATTACHMENT_ACTION_MARKERS):
        return "run"
    return "chat"
