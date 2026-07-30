"""基于已持久化事实生成终态复盘，不保存或展示隐藏推理。"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from yuwang.policy import redact

_FLAG = re.compile(r"(?i)[A-Za-z][A-Za-z0-9_-]{0,39}\{[^\s{}]{1,300}\}")
_URL = re.compile(r"https?://\S+")
_ARTIFACT = re.compile(r"(?i)artifact(?:\s*(?:id|编号))?\s*[:#]?\s*[0-9a-f-]{8,}")


class StepReview(BaseModel):
    """每项只评价一个已持久化步骤，不承载新的外部事实。"""

    model_config = ConfigDict(extra="forbid")

    step: int = Field(ge=1)
    assessment: str = Field(min_length=1, max_length=400)
    contribution: str = Field(min_length=1, max_length=600)


class RunRetrospectiveDraft(BaseModel):
    """模型的公开复盘契约。字段不允许携带 Flag、URL、Artifact 或验证结论。"""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1_000)
    outcome_review: str = Field(min_length=1, max_length=1_000)
    step_reviews: list[StepReview] = Field(default_factory=list, max_length=100)
    effective_actions: list[str] = Field(default_factory=list, max_length=12)
    failed_attempts: list[str] = Field(default_factory=list, max_length=12)
    lessons: list[str] = Field(default_factory=list, max_length=12)
    next_steps: list[str] = Field(default_factory=list, max_length=12)


class RunRetrospective(RunRetrospectiveDraft):
    """进入报告的复盘，source 明确模型调用是否实际完成。"""

    source: Literal["model", "deterministic"] = "deterministic"


def _clean_text(value: str, validation_status: str) -> str:
    """移除模型不应新增的标识，并避免把未验证事实写成验证通过。"""

    text = redact(" ".join(value.split()))
    text = _URL.sub("[已省略 URL]", text)
    text = _FLAG.sub("[已省略候选值]", text)
    text = _ARTIFACT.sub("[已省略 Artifact 引用]", text)
    if validation_status != "validated":
        for phrase in ("赛题平台验证通过", "平台验证通过", "外部验证通过", "验证通过"):
            text = text.replace(phrase, "尚未记录该验证")
    return text[:1_000]


def _clean_list(values: list[str], validation_status: str, limit: int = 12) -> list[str]:
    return [
        text
        for value in values[:limit]
        if (text := _clean_text(value, validation_status))
    ]


def deterministic_retrospective(facts: Any, reason: str | None = None) -> RunRetrospective:
    """模型不可用或历史报告缺字段时的事实摘要。"""

    timeline = list(getattr(facts, "timeline", []))
    failed = list(getattr(facts, "failed_attempts", []))
    status = str(getattr(facts, "validation_status", "pending"))
    step_reviews = [
        StepReview(
            step=int(step.get("sequence", index)),
            assessment="已记录执行结果。",
            contribution=str(step.get("observation_summary") or "该步骤未记录可公开观察。")[:600],
        )
        for index, step in enumerate(timeline, 1)
        if isinstance(step, dict)
    ]
    fallback = reason or "未完成模型复盘，以下内容由已持久化事实确定性生成。"
    return RunRetrospective(
        summary=fallback,
        outcome_review=f"事实记录的验证状态为：{status}。最终结论以报告中的确定性验证状态为准。",
        step_reviews=step_reviews,
        effective_actions=[str(step.get("action_summary")) for step in timeline if step.get("observation_status") == "success"][:6],
        failed_attempts=[str(step.get("observation_summary") or step.get("error") or "步骤未成功") for step in failed][:6],
        lessons=["仅依据已持久化的工具观察和验证状态形成结论。"],
        next_steps=["如需进一步验证，应执行任务已授权的独立验证步骤。"],
        source="deterministic",
    )


def merge_retrospective(
    facts: Any, draft: RunRetrospectiveDraft
) -> RunRetrospective:
    """清理模型文本、过滤非法步骤引用，并为每个真实步骤补齐覆盖。"""

    timeline = list(getattr(facts, "timeline", []))
    status = str(getattr(facts, "validation_status", "pending"))
    sequences = {
        int(step["sequence"])
        for step in timeline
        if isinstance(step, dict) and isinstance(step.get("sequence"), int)
    }
    reviews: dict[int, StepReview] = {}
    for review in draft.step_reviews:
        if review.step not in sequences or review.step in reviews:
            continue
        assessment = _clean_text(review.assessment, status)
        contribution = _clean_text(review.contribution, status)
        if assessment and contribution:
            reviews[review.step] = StepReview(
                step=review.step, assessment=assessment[:400], contribution=contribution[:600]
            )
    for step in timeline:
        if not isinstance(step, dict) or not isinstance(step.get("sequence"), int):
            continue
        sequence = step["sequence"]
        reviews.setdefault(
            sequence,
            StepReview(
                step=sequence,
                assessment="已由确定性逻辑补齐复盘覆盖。",
                contribution=str(step.get("observation_summary") or "该步骤未记录可公开观察。")[:600],
            ),
        )
    return RunRetrospective(
        summary=_clean_text(draft.summary, status) or "模型复盘未提供有效摘要。",
        outcome_review=(
            f"事实记录的验证状态为：{status}。"
            f" {_clean_text(draft.outcome_review, status)}"
        )[:1_000],
        step_reviews=[reviews[step] for step in sorted(reviews)],
        effective_actions=_clean_list(draft.effective_actions, status),
        failed_attempts=_clean_list(draft.failed_attempts, status),
        lessons=_clean_list(draft.lessons, status),
        next_steps=_clean_list(draft.next_steps, status),
        source="model",
    )
