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
) -> list[ToolSpec]:
    """返回下一次 Run 可见的交集；不修改当前注册表或历史快照。"""

    available = list(specs)
    allowed = {item.id for item in available}
    if profile_mode == "selected":
        allowed &= set(profile_tool_ids)
    if thread_mode == "selected":
        allowed &= set(thread_tool_ids)
    return [item for item in available if item.id in allowed]
