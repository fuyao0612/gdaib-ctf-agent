"""Agent Profile 与 Thread 的工具白名单校验和 Run 时快照过滤。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from .contracts import ToolSpec

ProfileToolSelectionMode = Literal["all", "selected"]
ThreadToolSelectionMode = Literal["inherit", "selected"]


def validate_tool_ids(tool_ids: Iterable[str], available_ids: set[str]) -> list[str]:
    """返回去重后的稳定 ID；未知或重复 ID 不能悄悄扩大工具范围。"""

    normalized = list(tool_ids)
    if len(normalized) != len(set(normalized)):
        raise ValueError("工具 ID 不能重复")
    unknown = sorted(set(normalized) - available_ids)
    if unknown:
        raise ValueError(f"工具不存在、已停用或不可用：{', '.join(unknown[:5])}")
    return normalized


def select_tool_specs(
    specs: Iterable[ToolSpec],
    *,
    profile_mode: ProfileToolSelectionMode,
    profile_tool_ids: Iterable[str],
    thread_mode: ThreadToolSelectionMode,
    thread_tool_ids: Iterable[str],
    scenario: str | None = None,
    capabilities: Iterable[str] = (),
    input_artifact_types: Iterable[str] = (),
) -> list[ToolSpec]:
    """返回下一次 Run 可见的交集；不修改当前注册表或历史快照。"""

    available = list(specs)
    allowed = {item.id for item in available}
    if profile_mode == "selected":
        allowed &= set(profile_tool_ids)
    if thread_mode == "selected":
        allowed &= set(thread_tool_ids)
    requested_caps = {str(value).casefold() for value in capabilities}
    input_types = {str(value).casefold() for value in input_artifact_types}
    authorized = [item for item in available if item.id in allowed]
    selected: list[ToolSpec] = []
    for item in available:
        if item.id not in allowed:
            continue
        if (
            scenario
            and item.scenarios
            and scenario not in item.scenarios
            and "general" not in item.scenarios
            and item.source_type != "builtin"
        ):
            continue
        if requested_caps and not requested_caps.intersection({value.casefold() for value in item.capabilities}):
            continue
        consumes = {value.casefold() for value in (item.consumes or item.artifact_types)}
        # 用户上传的 Artifact kind 通常是通用的 ``upload``，在 MIME/内容分析前
        # 不能据此排除候选工具；只有已知类型时才收窄候选。
        if input_types and "upload" not in input_types and consumes and not input_types.intersection(consumes):
            continue
        selected.append(item)
    # 元数据是候选收窄信号而不是隐式拒绝策略；没有任何匹配项时保留原有
    # 授权清单，交由 Agent 基于公开原因决定是否调用或请求补充材料。
    return selected or authorized
