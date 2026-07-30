"""Flag 候选的共享、受限判定，不能替代任何确定性或平台验证。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from yuwang.domain.models import VerificationRule
from yuwang.verification_rules import validate_verification_rule

# 仅接受常见比赛前缀，避免把任意 JSON、URL 或自然语言当成 Flag。
_GENERIC_FLAG = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(?:flag|gdaib|ctf|hack|pwn|crypto|web)\{[^\s{}]{1,300}\}"
)


def is_flag_candidate(value: object, rules: Iterable[VerificationRule] = ()) -> bool:
    """判断是否值得作为候选保存，结果绝不表示验证通过。"""

    candidate = str(value).strip()
    rules = list(rules)
    if rules:
        for rule in rules:
            try:
                validate_verification_rule(rule)
            except ValueError:
                continue
            if rule.kind == "regex" and re.fullmatch(rule.value, candidate):
                return True
            if rule.kind == "sha256" and hashlib.sha256(candidate.encode("utf-8")).hexdigest() == rule.value.lower():
                return True
        return False
    return bool(_GENERIC_FLAG.fullmatch(candidate))


def find_flag_candidates(text: object, rules: Iterable[VerificationRule] = ()) -> list[str]:
    """从持久化文本提取符合相同规则的候选，保持原始出现顺序。"""

    rules = list(rules)
    values = _GENERIC_FLAG.findall(str(text))
    return list(dict.fromkeys(value for value in values if is_flag_candidate(value, rules)))
