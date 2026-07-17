# 故障排查

## 服务可用性

- health 失败：检查容器状态、8000 内部端口、数据目录权限与只读根文件系统的 tmpfs。
- health 正常但 readiness 为 503：读取响应中的非敏感 `checks`，依次修复数据库、主密钥、管理员令牌或 Provider。
- Web 可打开但自动弹出首次配置：这是 Provider 未配置时的预期行为；使用 `.env` 中的管理员令牌登录并完成连接测试。
- 页面提示“管理员令牌不正确”：设置中心需要 `.env` 中 `YUWANG_ADMIN_TOKEN=` 等号后的完整值，不是 Provider API Key。不要粘贴变量名、引号或首尾空格；修改 `.env` 后执行 `docker compose up -d --force-recreate api`，仅 `restart` 不会重新注入环境变量。
- PowerShell 提示 `RandomNumberGenerator` 没有 `Fill` 方法：当前版应使用 `.\yuwang.ps1 setup`。该入口兼容 Windows PowerShell 5.1；若仍出现旧错误，先确认当前分支已包含最新版 `scripts/first-setup.ps1`。
- 本地开发提示端口 5173/8000 被占用：回到原启动窗口按 `Ctrl+C`；也可用 `Get-NetTCPConnection -LocalPort 5173,8000 -State Listen` 找到占用者。不要直接删除进程记录来绕过正在运行的实例。
- 本地开发显示启动后立即退出：查看 `data/logs/api.stderr.log` 和 `data/logs/web.stderr.log`，再运行 `.\scripts\start.ps1 -Development -CheckOnly` 检查版本、依赖、环境变量和端口。
- 只想确认环境：运行 `.\yuwang.ps1 doctor`；只检查启动参数可使用 `.\yuwang.ps1 start -CheckOnly` 或加 `-Development`，都不会启动服务。

## Provider 与运行

- 401/403：检查 API Key 权限和管理员会话；不要通过启用 fallback 绕过安全拒答。
- 模型不存在或结构化输出失败：刷新模型发现，核对厂商预设和结构化模式，必要时选择提示词兼容模式。
- 预算提前耗尽：在 Run 审计查看实际请求、重试、Token、费用和上下文裁剪；恢复不会重置消耗。
- `waiting_input`：普通模式需要补充信息后继续；竞赛模式按 Agent 配置重规划或失败，不会假装等待。
- 历史运行无法恢复：检查不可变 TaskSpec、Agent 快照和最后检查点；结果不确定的非幂等调用必须人工复核。
- 普通问题进入五阶段任务：确认输入区选中“对话”；`normal/competition` 是 Agent 限制，不是回复方式。
- 普通聊天没有助手消息：检查默认聊天模型是否已保存、Provider 是否连接成功，并在审计前先看 API 的聊天错误响应。
- 暂停按钮显示“暂停已排队”：请求已到达，系统会等待当前非幂等操作结束并在下一个安全检查点暂停。
- 指引一直显示“已排队”：运行尚未到安全节点；不要重复提交，序号与消费状态会在刷新后恢复。

## 数据与恢复

- Provider 无法解密：恢复时使用了错误的 `YUWANG_MASTER_KEY`。只能恢复正确的离线密钥，不能从密文推导。
- SQLite 锁或损坏：停止 API，保留现场副本后使用最近的一致性备份恢复；不要在运行中复制数据库文件。
- SSE 时间线缺口：浏览器会用事件序号重连，也可使用事件查询的 `after` 参数核对持久化序列。
- 页面无法下滑或输入框被裁切：先恢复浏览器缩放到 100%，再确认没有旧 CSS 缓存；当前验收范围为 1024×768 至 2048×1152。

提交诊断信息前先脱敏。允许提供版本、状态码、错误类别、事件序号和调用 ID；禁止提供 `.env`、Authorization/Cookie、API Key、完整提示词中的凭据、数据库或附件原文。
