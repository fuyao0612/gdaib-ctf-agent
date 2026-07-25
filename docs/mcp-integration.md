# MCP 集成

御网智元只集成 MCP Tools。Resources 和 Prompts 保留给后续版本，但不会在当前运行时加载。

## 配置方式

Stdio 使用独立的 `command` 和 `args` 字段，命令必须在管理员允许列表内；禁止 Shell 解释器、管道、重定向和拼接命令。Streamable HTTP 仅接受 HTTPS，开发测试可显式使用 localhost HTTP。

```json
{
  "name": "本地取证 MCP",
  "transport": "stdio",
  "command": "C:/approved/bin/forensic-mcp.exe",
  "args": ["--read-only"],
  "enabled": true,
  "connect_timeout_seconds": 10,
  "call_timeout_seconds": 30,
  "allowed_tools": ["inspect"],
  "blocked_tools": []
}
```

认证信息在服务端加密保存，读取接口只返回 `has_auth`，不会返回令牌原文。HTTP 目标会阻止 SSRF、localhost 绕过、云元数据地址和重绑定风险。

## 生命周期

1. 管理员在“工具与扩展”添加受控服务配置。
2. 健康检查与 `tools/list` 成功后，工具按 `mcp.<server-id>.<tool>` 注册。
3. 每个 MCP 工具仍经过本地 `PolicyEngine`、审批、超时、审计和 Run 快照。
4. 服务断开只影响该服务；可手动刷新并安全更新注册表，不影响历史 Run 快照。

MCP 的名称、描述、Schema 和输出都视为不可信数据。不得把它们视为系统指令、权限声明或可执行代码。
