# 模型 Provider

生产核心仅依赖 `ModelProvider.generate_structured`，`OpenAICompatibleProvider` 负责 HTTPS 请求、结构化输出、超时和有限重试。错误统一分类为鉴权、限流、超时、拒答、非法输出和服务异常；只有 408、429、500、502、503、504 会重试。客户端禁止跟随重定向，并启用 TLS 证书校验。

| 厂商 | Base URL | 示例模型 | 结构化模式 |
|---|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `deepseek-v4-flash` / `deepseek-v4-pro` | `json_object` |
| 阿里云百炼/千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3.7-plus` 等账户可用模型 | 非思考模式 `json_object` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-5.1` / `glm-5` / `glm-4.7` | `json_object` |

能力协商不会再假定所有厂商支持 `json_schema`。`auto` 会选择预设官方支持的模式；自定义端点可选 `json_schema`、`json_object` 或仅用提示词约束的 `prompt_json` 兼容模式。v0.2 中保存为 `json_schema` 的国内厂商配置会在读取时协商到 `json_object`，无需破坏性迁移。官方依据为 [DeepSeek JSON Output](https://api-docs.deepseek.com/zh-cn/guides/json_mode/)、[阿里云百炼结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output) 和 [智谱结构化输出](https://docs.bigmodel.cn/cn/guide/capabilities/struct-output)。

也可选择“自定义”接入实现 `/chat/completions` 的兼容服务。模型发现通过可选 `/models` 接口完成；不支持时仍可手动填写模型名称。生产配置必须使用 HTTPS；仅测试进程可显式设置 `YUWANG_ALLOW_INSECURE_LOCAL_PROVIDER=true` 使用 localhost HTTP。

Provider API Key 通过管理员接口录入，并使用 `YUWANG_MASTER_KEY` 派生的 Fernet 认证加密保存。列表、事件、报告和日志只显示脱敏状态，不返回明文。默认 Provider 后可按 `fallback_order` 选择备用项；一次 Run 会固化加密 Provider 快照，恢复或重试不会悄然切换配置。

每个 Provider 可配置允许 fallback 的错误类别。默认只对限流、超时和临时服务错误切换，不会因鉴权失败或安全拒答切换模型。`provider_retry_budget` 是整条链的额外请求上限，并与单 Provider 重试上限共同生效。审计记录实际 HTTP 请求数、重试数、最终 Provider/模型、延迟及服务返回的 Token usage；服务未返回 usage 时会明确标记并采用保守估算，不能伪装成厂商计量。

## 真实验收与无密钥环境

设置中心的“连接测试”会使用已保存的 Provider 配置发起一次真实请求；它只能说明地址、密钥、
模型和结构化协商在当时可连接，不能替代具体任务的验证证据。普通聊天、意图判断和结构化
Agent 调用应分别保留清晰的错误提示，且错误响应、审计和日志不得返回完整密钥。

自动化测试默认使用隔离协议服务，不把它称为真实厂商验收。真实兼容性矩阵分别覆盖 DeepSeek、
阿里云百炼/千问、智谱 GLM 与自定义 OpenAI 兼容接口；只有显式设置
`YUWANG_RUN_REAL_PROVIDER_TEST=1` 并为某一类配置隔离测试账户后，该类才会调用真实服务。
它会验证加密保存、连接、聊天、意图判断、Agent Run 和流式输出；缺少变量时结果是“跳过/未执行”，
不是通过。命令与 PR 记录要求见
[测试文档](testing.md#真实-provider-验收)。

不要把 API Key、主密钥或管理员令牌写进 `.env.example`、测试快照或提交历史。轮换主密钥前必须按[部署文档](deployment.md)备份并重新加密现有 Provider 密钥。
