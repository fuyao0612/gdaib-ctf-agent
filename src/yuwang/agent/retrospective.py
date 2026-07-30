"""基于已持久化事实生成终态复盘，不保存或展示隐藏推理。"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from yuwang.policy import redact

_FLAG = re.compile(r"(?i)[A-Za-z][A-Za-z0-9_-]{0,39}\{[^\s{}]{1,300}\}")
_URL = re.compile(r"https?://\S+")
_ARTIFACT = re.compile(r"(?i)artifact(?:\s*(?:id|编号))?\s*[:#]?\s*[0-9a-f-]{8,}")
_PATH = re.compile(r"(?<![A-Za-z0-9_])/(?:[A-Za-z0-9._~-]+/)*[A-Za-z0-9._~-]*")
_HOST_PORT = re.compile(r"\b(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]+:\d{2,5}\b")
_QUERY = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]{0,63}=[^\s,;]+")
_CALL_ID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", re.I)


def _validation_label(status: str) -> str:
    return {
        "pending": "待验证",
        "unverified": "未完成外部验证",
        "partial": "已完成部分校验，未完成外部验证",
        "validated": "已通过确定性验证",
        "failed": "验证失败",
    }.get(status, "验证状态未知")


class StepReview(BaseModel):
    """每项只评价一个已持久化步骤，不承载新的外部事实。"""

    model_config = ConfigDict(extra="forbid")

    step: int = Field(ge=1)
    assessment: str = Field(min_length=1, max_length=400)
    contribution: str = Field(min_length=1, max_length=600)
    fact_refs: list[str] = Field(default_factory=list, max_length=20)


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


def _clean_text(
    value: str, validation_status: str, allowed_identifiers: set[str] | None = None
) -> str:
    """移除模型不应新增的标识，并避免把未验证事实写成验证通过。"""

    text = redact(" ".join(value.split()))
    # 复盘文字可评价过程，却不能带入事实语料之外的地址、路径或调用标识。
    placeholders: dict[str, str] = {}
    for index, identifier in enumerate(sorted(allowed_identifiers or set(), key=len, reverse=True)):
        if identifier and identifier in text:
            token = f"__FACT_{index}__"
            placeholders[token] = identifier
            text = text.replace(identifier, token)
    text = _URL.sub("[已省略 URL]", text)
    text = _FLAG.sub("[已省略候选值]", text)
    text = _ARTIFACT.sub("[已省略 Artifact 引用]", text)
    text = _PATH.sub("[已省略路径]", text)
    text = _HOST_PORT.sub("[已省略地址]", text)
    text = _QUERY.sub("[已省略参数]", text)
    text = _CALL_ID.sub("[已省略调用标识]", text)
    for token, identifier in placeholders.items():
        text = text.replace(token, identifier)
    if validation_status != "validated":
        for phrase in ("赛题平台验证通过", "平台验证通过", "外部验证通过", "验证通过"):
            text = text.replace(phrase, "尚未记录该验证")
    return text[:1_000]


def _clean_list(
    values: list[str], validation_status: str, allowed_identifiers: set[str], limit: int = 12
) -> list[str]:
    return [
        text
        for value in values[:limit]
        if (text := _clean_text(value, validation_status, allowed_identifiers))
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
        outcome_review=f"事实记录的验证状态为：{_validation_label(status)}。最终结论以报告中的确定性验证状态为准。",
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
    fact_texts: dict[str, str] = {}
    allowed_identifiers: set[str] = set()
    for step in timeline:
        if not isinstance(step, dict) or not isinstance(step.get("sequence"), int):
            continue
        sequence = step["sequence"]
        action = str(step.get("action_summary") or step.get("goal") or "")
        if action:
            fact_texts[f"step:{sequence}:action"] = action
        for index, value in enumerate(step.get("observation_facts", []), 1):
            text = str(value).strip()
            if text:
                fact_texts[f"step:{sequence}:observation:{index}"] = text
    for index, value in enumerate(getattr(facts, "adjustments", []), 1):
        if str(value).strip():
            fact_texts[f"adjustment:{index}"] = str(value).strip()
    for value in fact_texts.values():
        allowed_identifiers.update(_URL.findall(value))
        allowed_identifiers.update(_FLAG.findall(value))
        allowed_identifiers.update(_PATH.findall(value))
        allowed_identifiers.update(_HOST_PORT.findall(value))
        allowed_identifiers.update(_QUERY.findall(value))
        allowed_identifiers.update(_CALL_ID.findall(value))
    reviews: dict[int, StepReview] = {}
    for review in draft.step_reviews:
        if review.step not in sequences or review.step in reviews:
            continue
        assessment = _clean_text(review.assessment, status, allowed_identifiers)
        valid_refs = list(dict.fromkeys(ref for ref in review.fact_refs if ref in fact_texts))
        contribution = "；".join(fact_texts[ref] for ref in valid_refs)
        # contribution 必须由真实事实渲染，模型仅保留对该步骤的公开评价。
        contribution = "；".join(fact_texts[ref] for ref in valid_refs)
        if not contribution:
            contribution = str(
                next(
                    (item.get("observation_summary") for item in timeline if item.get("sequence") == review.step),
                    "",
                )
            ) or "该步骤未记录可公开观察。"
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
        summary=_clean_text(draft.summary, status, allowed_identifiers) or "模型复盘未提供有效摘要。",
        outcome_review=(
            f"事实记录的验证状态为：{_validation_label(status)}。"
            f" {_clean_text(draft.outcome_review, status, allowed_identifiers)}"
        )[:1_000],
        step_reviews=[reviews[step] for step in sorted(reviews)],
        effective_actions=[
            str(step.get("action_summary"))
            for step in timeline if step.get("observation_status") == "success" and step.get("action_summary")
        ][:6],
        failed_attempts=[
            str(step.get("observation_summary") or step.get("error") or "步骤未成功")
            for step in getattr(facts, "failed_attempts", [])
        ][:6],
        lessons=_clean_list(draft.lessons, status, allowed_identifiers),
        next_steps=_clean_list(draft.next_steps, status, allowed_identifiers),
        source="model",
    )
