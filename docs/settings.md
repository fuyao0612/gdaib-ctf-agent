# 设置参考

设置中心默认使用新手模式，只展示厂商、API 地址、API Key、模型、连接测试和服务端
正式默认 Agent。切换高级模式后编辑的仍是同一份配置，不会产生第二套状态。

## Provider

Provider 配置包含厂商预设、Base URL、模型名、结构化输出模式、超时、重试、Token 单价、备用顺序和允许触发备用模型的错误类别。DeepSeek、千问和 GLM 预设按能力协商输出格式；自定义端点可手工选择 JSON Schema、JSON Object 或提示词兼容模式。安全拒答与鉴权失败默认不会触发 fallback。

API Key 只在创建或轮换时输入，以 `YUWANG_MASTER_KEY` 加密后保存在 SQLite；列表、导出、日志和浏览器都不返回明文。连接测试会真正检查鉴权、模型、结构化响应和 usage，测试协议服务只用于契约验证，不代表厂商联网成功。

## AgentProfile

每个 Agent 配置包含运行模式、Provider 链、分层提示词、规划策略、预算、上下文/记忆策略、声明式工作流、完成验证、人工介入和报告模板。保存编辑会创建新版本；Thread 与 Run 固定引用不可变快照，回滚也会创建后继版本，不改写历史。

完成模式：

- `advisory`：适合解释、总结、规划，明确标记为“模型生成，未经外部验证”。
- `structured`：必须通过配置的 JSON Schema。
- `evidence`：必须引用附件、工具或 Checker 证据，模型不能自行宣布成功。

## 预算与上下文

可限制总步骤、实际模型请求、工具调用、Token、模型费用、总时长、单步超时和 Provider 重试。上下文预算控制最近消息、摘要、记忆和安全文本附件；裁剪、摘要、请求、重试、费用和用量都会进入审计。重试、恢复和人工补充不会重置已消耗预算。

## 部署设置

`.env.example` 列出端口、数据路径、CORS、Secure Cookie 和资源限制。生产 HTTPS 必须使用准确的 HTTPS CORS 来源并设置 `YUWANG_COOKIE_SECURE=true`。不要提交 `.env`，不要在聊天、日志或工单中粘贴令牌或 API Key。
