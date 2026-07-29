"""Agent 公共入口；状态机实现细节留在子模块，调用方使用稳定门面。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

# reports.trace 只需要 repository 协议。包导入时若急切加载 engine，会经
# finalization 再次导入 reports.trace，形成循环；对外门面改为按需加载。
if TYPE_CHECKING:
    from .components import (
        AgentComponents,
        ContextBuilder,
        DefaultActionSelector,
        DefaultContextBuilder,
        DefaultPlanner,
        Memory,
        Planner,
        ReportRenderer,
        Verifier,
        WorkflowNode,
        default_components,
    )
    from .engine import AgentEngine
    from .repository import AgentRepository
    from .state import AgentStateModel, BudgetExceeded
    from .verification import SuccessVerifier, VerificationResult

__all__ = [
    "AgentEngine",
    "AgentStateModel",
    "BudgetExceeded",
    "AgentComponents",
    "AgentRepository",
    "ContextBuilder",
    "DefaultActionSelector",
    "DefaultContextBuilder",
    "DefaultPlanner",
    "Memory",
    "Planner",
    "ReportRenderer",
    "SuccessVerifier",
    "VerificationResult",
    "Verifier",
    "WorkflowNode",
    "default_components",
]

_EXPORTS = {
    "AgentEngine": ".engine",
    "AgentStateModel": ".state",
    "BudgetExceeded": ".state",
    "SuccessVerifier": ".verification",
    "VerificationResult": ".verification",
    "AgentComponents": ".components",
    "ContextBuilder": ".components",
    "DefaultActionSelector": ".components",
    "DefaultContextBuilder": ".components",
    "DefaultPlanner": ".components",
    "Memory": ".components",
    "Planner": ".components",
    "ReportRenderer": ".components",
    "Verifier": ".components",
    "WorkflowNode": ".components",
    "default_components": ".components",
    "AgentRepository": ".repository",
}


def __getattr__(name: str) -> Any:
    """按需加载公共符号，避免协议导入意外启动完整状态机。"""

    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
