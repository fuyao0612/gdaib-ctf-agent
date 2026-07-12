# Tool 开发与注册

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

参考实现位于 `src/yuwang/tooling/sdk.py`：`file_metadata` 和 `localhost_http_probe`。测试专用工具仅放在 `tests/`，不会注册到生产运行时或复制进生产镜像。
