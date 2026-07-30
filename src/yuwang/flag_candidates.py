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
_MAX_SCAN_CHARS = 20_000
_MAX_MATCHES = 32
_MAX_CANDIDATE_CHARS = 1_000


def is_flag_candidate(value: object, rules: Iterable[VerificationRule] = ()) -> bool:
    """判断是否值得作为候选保存，结果绝不表示验证通过。"""

    candidate = str(value).strip()
    if not candidate or len(candidate) > _MAX_CANDIDATE_CHARS:
        return False
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


def find_flag_candidates(
    text: object,
    rules: Iterable[VerificationRule] = (),
    *,
    allow_whole_text_sha256: bool = False,
) -> list[str]:
    """从持久化文本提取符合相同规则的候选，保持原始出现顺序。"""

    raw = str(text)
    if len(raw) > _MAX_SCAN_CHARS:
        raw = raw[:_MAX_SCAN_CHARS]
    rules = list(rules)
    if not rules:
        return list(dict.fromkeys(_GENERIC_FLAG.findall(raw)[:_MAX_MATCHES]))

    values: list[str] = []
    for rule in rules:
        try:
            validate_verification_rule(rule)
        except ValueError:
            continue
        if rule.kind == "regex":
            # 规则已在统一入口校验；仍限制文本、数量和候选长度，避免展示层被大输出拖垮。
            for match in re.finditer(rule.value, raw):
                candidate = match.group(0)
                if 0 < len(candidate) <= _MAX_CANDIDATE_CHARS:
                    values.append(candidate)
                if len(values) >= _MAX_MATCHES:
                    break
        elif rule.kind == "sha256" and allow_whole_text_sha256:
            # SHA-256 不枚举任意子串，只允许调用方明确声明这是完整最终答案。
            candidate = raw.strip()
            if is_flag_candidate(candidate, [rule]):
                values.append(candidate)
        if len(values) >= _MAX_MATCHES:
            break
    return list(dict.fromkeys(value for value in values if is_flag_candidate(value, rules)))
