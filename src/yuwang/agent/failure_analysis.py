"""失败收口的可展示摘要与可选模型复盘。"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from yuwang.model_providers import ProviderError
from yuwang.policy import redact

FAILURE_SUMMARY_DETAIL_LIMIT = 800


class FailureAnalysisDraft(BaseModel):
    """模型只返回用户可见的复盘要点，绝不请求或保存隐藏推理。"""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1_000)
    causes: list[str] = Field(default_factory=list, max_length=4)
    next_steps: list[str] = Field(default_factory=list, max_length=4)


class FailureAnalysis(FailureAnalysisDraft):
    """持久化到失败事件和报告的结构化失败说明。"""

    source: Literal["deterministic", "model"] = "deterministic"


def deterministic_failure_analysis(error: BaseException | str) -> FailureAnalysis:
    """为所有异常生成非空、可行动且脱敏的基础说明。"""

    message = error.strip() if isinstance(error, str) else str(error).strip()
    error_type = type(error).__name__ if not isinstance(error, str) else "运行错误"
    if isinstance(error, ProviderError):
        category = str(error.category)
        descriptions = {
            "auth": "模型服务认证失败，无法继续运行",
            "rate_limit": "模型服务触发限流，无法在当前预算内继续运行",
            "timeout": "模型服务请求超时，运行已安全终止",
            "refusal": "模型服务拒绝处理当前请求",
            "invalid_output": "模型返回的结构化结果不符合协议",
            "service": "模型服务暂时不可用",
        }
        summary = descriptions.get(category, "模型服务调用失败")
        detail = _failure_detail(message, "Provider 未返回详细错误")
        next_steps = {
            "auth": ["检查 Provider API Key、服务地址和模型名称后重试"],
            "rate_limit": ["稍后重试，或降低并发与调用预算"],
            "timeout": ["检查 Provider 连通性，必要时适当提高步骤超时"],
            "refusal": ["检查任务描述和 Provider 内容策略后重试"],
            "invalid_output": ["检查 Provider 的结构化输出兼容配置后重试"],
            "service": ["检查 Provider 服务状态和网络连接后重试"],
        }.get(category, ["检查 Provider 配置和运行审计后重试"])
        return FailureAnalysis(
            summary=f"{summary}：{detail}",
            causes=[f"Provider 错误类别：{category}"],
            next_steps=next_steps,
        )
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return FailureAnalysis(
            summary="模型调用或工作流步骤超时，运行已安全终止。",
            causes=["等待时间超过本次 Run 配置的步骤超时限制"],
            next_steps=["检查 Provider 连通性与模型负载，必要时适当提高步骤超时后重试"],
        )

    detail = _failure_detail(message, f"{error_type} 未返回错误详情")
    return FailureAnalysis(
        summary=f"运行已安全终止：{detail}",
        causes=[f"异常类型：{error_type}"],
        next_steps=["根据运行审计中的失败节点修正任务信息或配置后重试"],
    )


def _failure_detail(message: str, fallback: str) -> str:
    """异常文本可能来自第三方校验器，限制长度以保证失败复盘总能持久化。"""

    detail = redact(message) if message else fallback
    return detail[:FAILURE_SUMMARY_DETAIL_LIMIT]


def allows_model_failure_analysis(error: BaseException | str) -> bool:
    """Provider/超时本身失败时不能再请求模型，避免额外消耗或递归失败。"""

    return not isinstance(error, (ProviderError, asyncio.TimeoutError, TimeoutError))


def merge_model_failure_analysis(
    fallback: FailureAnalysis, draft: FailureAnalysisDraft
) -> FailureAnalysis:
    """模型复盘不完整或包含敏感文本时，始终回退到确定性结论。"""

    summary = redact(draft.summary.strip()) or fallback.summary
    causes = [redact(item.strip()) for item in draft.causes if item.strip()][:4] or fallback.causes
    next_steps = [redact(item.strip()) for item in draft.next_steps if item.strip()][:4] or fallback.next_steps
    return FailureAnalysis(
        summary=summary,
        causes=causes,
        next_steps=next_steps,
        source="model",
    )
