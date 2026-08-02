# 升级指南

## 从 v0.4.2 升级到 v0.5.0

当前版本改为纯任务 Agent。`Thread.interaction_mode` 和 `chat_requests` 表只为历史 SQLite 兼容
保留，运行时不再读取或写入普通聊天请求；旧 Message、Run、Event、Report 和 Provider 不会被
删除或重写。旧 Thread 缺少字段时补为已弃用的兼容值，新 Thread 始终从受控 Run 开始。

领域契约扩展记录在 SQLite `schema_migrations` 版本 13。Artifact、Run 结果和评测准则继续以 JSON
保存；旧行缺少新字段时使用兼容默认值，不会删除或重写历史数据。升级后可直接读取旧 CTF 报告、
`submitted_flag` 和旧计划，新的 Run 使用 `TaskResult`、结构化计划步骤和人机交接摘要。

旧数据库中 `context_token_budget=32000` 会在模型校验前幂等迁移为 32768；新安装仍默认 262144，
用户已保存的 32768 不会被改写为 262144。

1. 执行 `scripts/backup.ps1`，同时保留原 `.env`、SQLite 和附件目录。
2. 拉取 v0.5.0 后运行 `.\yuwang.ps1 check`，再使用 `start -Build` 重建镜像。
3. 登录设置中心，保存 Agent 默认设置，重新执行一次真实 Provider 连接测试。
4. 抽查旧 Agent Thread 的 Task Brief、计划、暂停状态、指引、报告和审计仍可读取。
5. 新建任务，发送任意任务描述并刷新，确认已创建 Run、模型消息、报告和审计；再完成一次暂停/继续验收。

回滚应用前必须恢复升级前一致性备份，不能用删除新表或清空数据库代替回滚。

## 从 v0.3 升级到 v0.4

v0.4 保持原 SQLite JSON 行可读，并在模型校验时为新增的 Provider 连接状态、上下文计数和助手角色提供兼容默认值。原 v0.3 工作流节点列表会映射为最接近的 `direct`、`planned` 或 `verified` 安全预设。升级前必须备份 `data/` 与 `.env`，升级后应重新对默认 Provider 执行真实连接测试；在此之前就绪探针会保持 `not_ready`，避免把“已启用但不可用”的模型投入生产。

1. 执行 `scripts/backup.ps1`（Linux/macOS 使用 `.sh`）并验证备份可读。
2. 保留原 `YUWANG_MASTER_KEY`，拉取 v0.4 后运行迁移脚本。
3. 启动服务、登录设置中心，测试默认 Provider 并确认默认 Agent 能解析该 Provider。
4. 验证旧对话、报告和审计可读，再执行一轮连续对话与刷新恢复验收。

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
