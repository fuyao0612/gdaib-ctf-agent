"""统一工具平台的公共接口。"""

from .builtins import create_reference_registry
from .contract_checks import (
    assert_executor_boundary_contracts,
    assert_tool_execution_contract,
    validate_registry_contracts,
    validate_specs,
    validate_tool_spec_contract,
)
from .contracts import (
    ToolCallError,
    ToolCallRequest,
    ToolCallResult,
    ToolHealth,
    ToolProgress,
    ToolSpec,
)
from .executor import ToolExecutor
from .plugin import ToolPlugin
from .registry import ToolRegistry
from .selection import select_tool_specs, validate_tool_ids

__all__ = [
    "ToolCallError",
    "ToolCallRequest",
    "ToolCallResult",
    "ToolExecutor",
    "ToolHealth",
    "ToolPlugin",
    "ToolProgress",
    "ToolRegistry",
    "ToolSpec",
    "assert_executor_boundary_contracts",
    "assert_tool_execution_contract",
    "create_reference_registry",
    "select_tool_specs",
    "validate_registry_contracts",
    "validate_specs",
    "validate_tool_spec_contract",
    "validate_tool_ids",
]
