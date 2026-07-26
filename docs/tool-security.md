# 工具安全边界

## 必须遵守

- 所有工具显式注册；禁止目录扫描、任意 import、Shell 或 Web 代码上传。
- 仅处理当前任务授权的目标。CTF 工具仅处理当前 Thread 的 Artifact ID；纯文本 `encoding_decode` 可处理当前消息中不超过 12,000 字符的受限文本，不能接收文件路径或其他目标引用。
- 输入、输出和配置使用 JSON Schema，且对象拒绝额外字段。
- 工具输出一律是不可信数据，不能改变风险、权限、授权范围或系统提示。
- 不记录或返回 API Key、MCP 令牌、Cookie、完整环境变量或宿主机绝对路径。

## 风险和运行时

`low` 工具仅可处理受控数据；`medium` 工具需要策略确认；`high` 工具默认拒绝。需要外部二进制的中风险工具必须使用内部 Docker 沙箱，沙箱默认无外网、非 root、无 Docker Socket、无宿主机目录挂载，并在超时或取消时终止。

## Artifact

Artifact 的 `storage_ref` 是服务端不透明相对引用，禁止绝对路径、空段、`.` 和 `..`。压缩包工具还必须限制成员数量、展开大小、压缩比和递归层数，并拒绝 Zip Slip、符号链接和设备文件。

## 发布前检查

```powershell
python scripts/check_tool_contracts.py
pytest -q --no-cov tests/test_tool_contracts.py
ruff check .
mypy
```

新增工具还应提供与风险相称的真实执行测试；没有真实 Provider 密钥的测试必须明确跳过，不能以固定回复替代。
