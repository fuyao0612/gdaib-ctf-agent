# 协作规范

稳定分支为 `main`，日常集成进入 `develop`；功能使用 `feature/<topic>`，修复使用 `fix/<topic>`。从最新 `develop` 建短生命周期分支，通过 PR 合并；紧急修复合入 `main` 后同步回 `develop`。不要改写共享历史或提交密钥、运行数据和构建产物。

日常提交前运行 `.\scripts\check.ps1`；准备合并时运行 `.\scripts\full-check.ps1`，一次覆盖 Playwright、生产表面、Compose 配置和 Windows 启动安全验收。Schema、事件或 Tool/Provider 契约变更必须说明兼容性并补契约测试。安全相关变更必须说明授权范围、默认行为、凭据和文件/网络风险。

PR 使用仓库模板，完整填写改动内容、实际测试证据、兼容性、风险以及截图/脱敏日志。至少一名维护者审核；架构或安全边界变更需要领域负责人审核。
