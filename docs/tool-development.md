# Tool 开发与注册

完整贡献指南已迁移至 [工具 SDK 指南](tool-sdk.md)。其中包含首个工具、ToolSpec 字段、风险选择、Artifact、进度、取消、沙箱、MCP 和契约测试说明；本页保留原始最小示例，供旧链接继续可用。

工具是 `ToolPlugin[I, O]`，必须提供 Pydantic 输入/输出类型和完整 `ToolSpec`：名称、语义版本、描述、能力、场景、风险、权限、网络需求、目标类型、超时、错误码、幂等性和产物类型。

```python
class CountInput(BaseModel):
    text: str

class CountOutput(BaseModel):
    characters: int

class CharacterCountTool(ToolPlugin[CountInput, CountOutput]):
    input_model = CountInput
    output_model = CountOutput

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="character_count", version="1.0.0", description="统计字符数",
            capabilities=["text"], scenarios=["general"], risk="low",
            permissions=[], requires_network=False, allowed_target_types=[],
            timeout_seconds=2, error_codes=[], idempotent=True, artifact_types=[],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute(self, value: CountInput) -> CountOutput:
        return CountOutput(characters=len(value.text))
```

在组装层调用 `registry.register(CharacterCountTool())`，无需修改 Agent 状态机。外部包也可声明 `yuwang.tools` entry point，再由组装层显式调用 `registry.discover()`；发现范围不会扫描任意模块。必须添加契约测试：Spec JSON Schema 可序列化、输入拒绝额外字段、标准化结果、异常隔离、超时和策略默认拒绝。不得接受 Shell 命令；网络工具必须由 `PolicyEngine` 校验明确授权目标。

参考实现位于 `src/yuwang/tooling/sdk.py`：`file_metadata` 和 `localhost_http_probe`。生产内置工具还包含只读 JWT 研判与 PCAP/PCAPNG 离线流量摘要；它们只接收当前 Run 授权的 `artifact_id`，不验签、不猜密钥、不重放流量。测试专用工具仅放在 `tests/`，不会注册到生产运行时或复制进生产镜像。

`localhost_http_probe` 可用于用户明确授权的本机 CTF 服务。它只能执行无请求体的只读 `GET`、`HEAD` 或 `OPTIONS`，仅接受
`http://localhost` 或 `http://127.0.0.1` 且必须匹配当前 Run 的授权目标；禁用代理和重定向，并限制
响应大小与超时。它只返回文本/JSON 摘要、白名单响应头、HTML 中已声明的同源链接及 `robots.txt` 的
声明路径，不会枚举路径、猜测认证或提交表单。若公开说明明确给出 CTF 请求头，调用者可传入一个
`X-CTF-*` 头；不得传入 Cookie、Authorization 或任意自定义请求头。文本响应会成为当前 Run 的
`http_evidence` Artifact，后续 `encoding_decode` 可通过 `artifact_id` 和受限 `json_pointer` 解码 JSON
字符串字段，`flag_candidate_verify` 仍只输出候选 Flag 的格式与证据来源结论。
