"""确定性证据规则的共享校验。

规则既会来自版本化 Agent 配置，也会出现在历史 Run 快照中。这里集中拒绝
“任意非空文本即成功”一类规则，避免不同入口对成功语义产生分歧。
"""

from __future__ import annotations

import re

from yuwang.domain.models import VerificationRule

_UNRELATED_PROBES = (
    "ordinary",
    "answer",
    "ordinary unrelated text",
    "__unrelated_candidate__",
    "普通无关文本",
    "1234567890",
)


def validate_verification_rule(rule: VerificationRule) -> VerificationRule:
    """确认规则是可执行且足够具体的外部验证条件。

    不能从通用正则自动推断任务目标，因此匹配一组无关普通文本的规则不能用于把
    结果标记为“已外部验证”。SHA-256 是精确值比较，不受这一限制。
    """

    if rule.kind != "regex":
        return rule
    try:
        compiled = re.compile(rule.value)
    except re.error as exc:
        raise ValueError("证据正则表达式无效") from exc
    if any(compiled.fullmatch(value) for value in _UNRELATED_PROBES):
        raise ValueError("证据正则不能匹配无关普通文本，请填写具体格式或固定摘要")
    return rule
