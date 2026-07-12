# 模型 Provider

生产核心仅依赖 `ModelProvider.generate_structured`，`OpenAICompatibleProvider` 负责 HTTPS 请求、结构化 JSON Schema/JSON Object 输出、超时和有限重试。错误统一分类为鉴权、限流、超时、拒答、非法输出和服务异常；只有 408、429、500、502、503、504 会重试。客户端禁止跟随重定向，并启用 TLS 证书校验。

| 厂商 | Base URL | 示例模型 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `deepseek-v4-flash` |
| 阿里云百炼/千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 按账户可用模型填写 |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | 按账户可用模型填写 |

也可选择“自定义”接入实现 `/chat/completions` 的兼容服务。生产配置必须使用 HTTPS；仅测试进程可显式设置 `YUWANG_ALLOW_INSECURE_LOCAL_PROVIDER=true` 使用 localhost HTTP。

Provider API Key 通过管理员接口录入，并使用 `YUWANG_MASTER_KEY` 派生的 Fernet 认证加密保存。列表、事件、报告和日志只显示脱敏状态，不返回明文。默认 Provider 后可按 `fallback_order` 选择备用项；一次 Run 会固化加密 Provider 快照，恢复或重试不会悄然切换配置。

不要把 API Key、主密钥或管理员令牌写进 `.env.example`、测试快照或提交历史。轮换主密钥前必须按[部署文档](deployment.md)备份并重新加密现有 Provider 密钥。
