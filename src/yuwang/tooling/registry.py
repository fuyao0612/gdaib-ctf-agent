"""工具注册、命名空间隔离和受控 entry point 发现。"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Iterable
from typing import Any

from .contracts import ToolHealth, ToolSpec
from .plugin import ToolPlugin


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolPlugin[Any, Any]] = {}
        self._aliases: dict[str, str] = {}
        self._ambiguous_aliases: set[str] = set()
        self._health: dict[str, ToolHealth] = {}

    def register(self, tool: ToolPlugin[Any, Any]) -> None:
        spec = tool.spec
        tool_id = spec.id
        if tool_id in self._tools:
            raise ValueError(f"工具 ID 冲突：{tool_id}")
        if not spec.enabled:
            self._health[tool_id] = ToolHealth(status="disabled")
            return
        self._tools[tool_id] = tool
        self._health[tool_id] = spec.health
        previous = self._aliases.get(spec.name)
        if previous is None and spec.name not in self._ambiguous_aliases:
            self._aliases[spec.name] = tool_id
        elif previous != tool_id:
            self._aliases.pop(spec.name, None)
            self._ambiguous_aliases.add(spec.name)

    def get(self, reference: str) -> ToolPlugin[Any, Any]:
        tool_id = self._aliases.get(reference, reference)
        if reference in self._ambiguous_aliases:
            raise KeyError("工具名称存在命名空间冲突，请使用完整工具 ID")
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise KeyError("工具未注册、已停用或不可用") from exc

    def names(self) -> set[str]:
        """返回稳定工具 ID；调用方不应依赖可能冲突的短名称。"""

        return set(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def health(self, reference: str) -> ToolHealth:
        tool = self.get(reference)
        return self._health.get(tool.spec.id, tool.spec.health)

    def all_health(self) -> dict[str, ToolHealth]:
        return dict(self._health)

    def unregister_source(self, source: str) -> None:
        """在服务刷新或删除时移除同一来源的当前工具，不影响历史 Run 快照。"""

        removed_ids = [tool_id for tool_id, tool in self._tools.items() if tool.spec.source == source]
        for tool_id in removed_ids:
            self._tools.pop(tool_id, None)
            self._health.pop(tool_id, None)
        self._aliases.clear()
        self._ambiguous_aliases.clear()
        for tool_id, tool in self._tools.items():
            name = tool.spec.name
            previous = self._aliases.get(name)
            if previous is None and name not in self._ambiguous_aliases:
                self._aliases[name] = tool_id
            elif previous != tool_id:
                self._aliases.pop(name, None)
                self._ambiguous_aliases.add(name)

    def discover(
        self,
        enabled_plugins: Iterable[str],
        group: str = "yuwang.tools",
    ) -> int:
        """仅加载管理员明确允许的安装包 entry point，不扫描本地目录。"""

        enabled = set(enabled_plugins)
        discovered = importlib.metadata.entry_points().select(group=group)
        loaded = 0
        for entry_point in discovered:
            if entry_point.name not in enabled:
                continue
            try:
                factory = entry_point.load()
                tool = factory() if callable(factory) else factory
                if not isinstance(tool, ToolPlugin):
                    raise TypeError("入口点未返回 ToolPlugin")
                self.register(tool)
                loaded += 1
            except Exception as exc:
                # 导入边界隔离，错误只留给管理面，绝不阻断其他工具启动。
                self._health[f"python_plugin.{entry_point.name}"] = ToolHealth(
                    status="unavailable", last_error=str(exc)[:500]
                )
        return loaded
