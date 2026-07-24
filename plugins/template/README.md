# Python 工具模板

复制此目录后重命名包、`manifest.json` 和 `ToolSpec.namespace`。入口点必须是无参数工厂，返回一个真实的 `ToolPlugin` 实例。包级 Manifest 只描述入口和兼容性；工具名称、权限和 Schema 只在 `ToolSpec` 中维护。

运行模板测试前，将 `src/` 加入 Python 路径或按标准 Python 包方式安装。完整要求见 [工具 SDK 指南](../../docs/tool-sdk.md)。
