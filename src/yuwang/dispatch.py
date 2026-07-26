"""统一消息入口的确定性控制命令和模型语义意图判断。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yuwang.domain.models import RunStatus

ActiveMessageRoute = Literal["stop", "guidance", "input", "clarification"]
MessageIntentKind = Literal["chat", "run", "clarify"]

_STOP_COMMANDS = {"停止", "停止生成", "停止任务", "取消", "终止", "stop", "cancel"}
_EXPLANATION_MARKERS = ("不要执行", "只解释", "仅解释", "只说明", "如何", "为什么", "是什么")
_EXECUTION_MARKERS = (
    "执行",
    "运行",
    "完成",
    "调用",
    "使用",
    "工具",
    "分析",
    "检查",
    "验证",
    "提取",
    "解码",
    "解压",
    "处理附件",
)
_AMBIGUOUS_REQUESTS = ("帮我处理", "帮我看看", "处理一下", "看一下这个")
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


class MessageIntent(BaseModel):
    """模型对一条新消息的唯一允许输出，额外字段和模糊结果都会被拒绝。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: MessageIntentKind
    clarification_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_clarification_question(self) -> MessageIntent:
        question = self.clarification_question.strip() if self.clarification_question else None
        if self.kind == "clarify" and not question:
            raise ValueError("需要澄清时必须给出具体问题")
        if self.kind != "clarify" and question is not None:
            raise ValueError("只有需要澄清时才能返回澄清问题")
        self.clarification_question = question
        return self


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


async def classify_new_message(
    content: str,
    *,
    has_attachments: bool,
    recent_messages: list[dict[str, str]],
) -> MessageIntent:
    """用保守规则分派新消息，不在 Agent 前额外调用模型。

    明确执行才会创建 Run；之后是否调用工具仍完全由同一个 Agent 模型循环决定。
    历史只用于识别用户明确要求继续上一个任务，不用于扩大授权范围。
    """

    normalized = "".join(content.casefold().split()).translate(_PUNCTUATION_TRANSLATION)
    if any(marker in normalized for marker in _EXPLANATION_MARKERS):
        return MessageIntent(kind="chat")
    if any(marker in normalized for marker in _AMBIGUOUS_REQUESTS):
        return MessageIntent(kind="clarify", clarification_question="请补充目标和预期交付物。")
    if "继续刚才" in normalized or normalized.startswith("继续"):
        previous_user_messages = [
            item.get("content", "") for item in recent_messages if item.get("role") == "user"
        ]
        if any(marker in "".join(previous_user_messages) for marker in ("准备", "安排", "计划")):
            return MessageIntent(kind="run")
    if has_attachments or any(marker in normalized for marker in _EXECUTION_MARKERS):
        return MessageIntent(kind="run")
    return MessageIntent(kind="chat")
