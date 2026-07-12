# 模型 Provider

核心只依赖 `ModelProvider.generate_structured`。Provider 必须返回调用方指定的 Pydantic 类型，并把鉴权、限流、超时、拒答、非法输出和服务错误映射到 `ProviderErrorCategory`。Agent 仅有限重试可重试错误。`ProviderChain` 按配置顺序完成 fallback，不在 Agent 核心写厂商分支。

Mock Provider 无密钥、完全确定性，可模拟成功、非法结构、拒答、超时和先失败后成功。兼容 Provider 从环境读取：

```dotenv
OPENAI_COMPATIBLE_BASE_URL=https://approved.example/v1
OPENAI_COMPATIBLE_API_KEY=本机密钥
OPENAI_COMPATIBLE_MODEL=approved-model
```

密钥不得提交、写数据库、事件、报告或前端。新增 DeepSeek、千问或 GLM 时实现相同协议，在 API 组装层注册及配置 fallback；不要修改 `AgentEngine`。为新实现测试结构化校验、所有错误分类、超时、重试和脱敏元数据。
