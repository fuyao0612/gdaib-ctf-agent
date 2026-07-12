# 升级指南

## 从 v0.2 升级到 v0.3

v0.3 启动时会执行增量 SQLite 迁移，为现有数据库增加 Provider 能力字段、AgentProfile 版本/快照、上下文记忆、完成可信等级与调用计量。现有 Thread、Message、Run、Event、Artifact 和报告保持可读；旧 Thread 会关联迁移生成的默认安全 Agent 快照。Provider 密文继续使用原 `YUWANG_MASTER_KEY`，绝不能在升级时生成新主密钥覆盖旧值。

推荐流程：

1. 停止写入并运行 `scripts/backup.ps1` 或 `scripts/backup.sh`。
2. 保存当前镜像标签和 `.env` 的离线副本，运行 `preflight`。
3. 拉取 v0.3 后运行 `migrate`，再构建并启动。
4. 检查 health 为 200；配置至少一个 Provider 后检查 readiness 为 200。
5. 抽查旧 Thread、事件、报告和 Provider 列表，再创建新 AgentProfile 做一次协议测试任务。

应用回滚不能回滚数据库。若必须回到 v0.2，请停止服务并恢复升级前的一致性数据备份及对应主密钥。不要对已迁移数据库直接启动旧镜像。

## 配置变化

新增 `YUWANG_WEB_PORT`、`YUWANG_DATA_PATH`、`YUWANG_COOKIE_SECURE` 和资源限制变量。Compose 从命名卷改为显式持久化路径；升级前确认 `YUWANG_DATA_PATH` 指向已恢复的数据目录。首次部署脚本不会覆盖已有 `.env`。
