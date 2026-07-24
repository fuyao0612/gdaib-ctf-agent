"""受控工具 SDK 的向后兼容入口。

新代码应按职责从 ``contracts``、``registry``、``executor`` 与 ``plugin`` 导入；
此模块保留历史插件的导入路径，避免升级破坏第三方工具包。
"""

from .builtins import (
    FileMetadataInput,
    FileMetadataOutput,
    FileMetadataTool,
    LocalhostHTTPProbeTool,
    ProbeInput,
    ProbeOutput,
    create_reference_registry,
)
from .contracts import (
    ToolCallError,
    ToolCallRequest,
    ToolCallResult,
    ToolError,
    ToolHealth,
    ToolProgress,
    ToolResult,
    ToolSpec,
)
from .executor import ToolExecutor
from .plugin import ToolPlugin
from .registry import ToolRegistry

__all__ = [
    "FileMetadataInput",
    "FileMetadataOutput",
    "FileMetadataTool",
    "LocalhostHTTPProbeTool",
    "ProbeInput",
    "ProbeOutput",
    "ToolCallError",
    "ToolCallRequest",
    "ToolCallResult",
    "ToolError",
    "ToolExecutor",
    "ToolHealth",
    "ToolPlugin",
    "ToolProgress",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "create_reference_registry",
]
