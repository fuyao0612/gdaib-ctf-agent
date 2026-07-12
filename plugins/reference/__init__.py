"""Reference plugin package.

The built-in implementations live in :mod:`yuwang.tooling.sdk` so source and
installed wheels share one contract. Deployment-specific registries may import
and register them from their composition root without changing AgentEngine.
"""

from yuwang.tooling.sdk import FileMetadataTool, LocalhostHTTPProbeTool, MockEchoTool

__all__ = ["FileMetadataTool", "LocalhostHTTPProbeTool", "MockEchoTool"]
