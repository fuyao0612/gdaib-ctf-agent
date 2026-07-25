"""工具白名单的纯过滤规则：Profile 授权是上界，Thread 只能继续收缩。"""

from __future__ import annotations

import pytest

from yuwang.tooling import ToolSpec, select_tool_specs, validate_tool_ids


def spec(name: str) -> ToolSpec:
    return ToolSpec(
        namespace="test",
        name=name,
        version="1.0.0",
        description="测试工具",
        risk="low",
        requires_network=False,
        idempotent=True,
        timeout_seconds=5,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )


def test_tool_selection_uses_profile_as_upper_bound_and_thread_as_intersection() -> None:
    first, second = spec("first"), spec("second")

    selected = select_tool_specs(
        [first, second],
        profile_mode="selected",
        profile_tool_ids=[first.id],
        thread_mode="inherit",
        thread_tool_ids=[],
    )
    narrowed = select_tool_specs(
        [first, second],
        profile_mode="all",
        profile_tool_ids=[],
        thread_mode="selected",
        thread_tool_ids=[second.id],
    )

    assert [item.id for item in selected] == [first.id]
    assert [item.id for item in narrowed] == [second.id]
    with pytest.raises(ValueError, match="不能重复"):
        validate_tool_ids([first.id, first.id], {first.id, second.id})
    with pytest.raises(ValueError, match="不可用"):
        validate_tool_ids(["test.missing"], {first.id, second.id})
