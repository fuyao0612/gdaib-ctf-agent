# 工具 SDK 指南

本指南面向为御网智元新增本地 Python 工具的贡献者。工具必须继承 `ToolPlugin`，经 `ToolRegistry` 注册，并始终由 `ToolExecutor` 统一校验、超时控制和异常隔离。

## 15 分钟首个工具

1. 从 `plugins/template/` 复制包结构，并将包名、入口点和工具命名空间改为自己的名称。
2. 定义 `extra="forbid"` 的 Pydantic 输入和输出模型。
3. 实现 `ToolPlugin`，在 `spec` 中返回完整 `ToolSpec`，并只在 `execute` 中编写业务逻辑。
4. 为发行包声明 `yuwang.tools` entry point；平台只发现管理员明确启用的 entry point，不会扫描目录或导入上传文件。
5. 运行 `python scripts/check_tool_contracts.py`，并在插件测试中调用 `assert_tool_execution_contract`。

```python
from pydantic import BaseModel, ConfigDict, Field
from yuwang.tooling import ToolPlugin, ToolSpec

class CountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=10_000)

class CountOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    characters: int

class CharacterCountTool(ToolPlugin[CountInput, CountOutput]):
    input_model = CountInput
    output_model = CountOutput

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            namespace="example",
            name="character_count",
            version="1.0.0",
            description="统计用户提供文本的字符数",
            capabilities=["text"],
            scenarios=["general"],
            risk="low",
            permissions=[],
            requires_network=False,
            allowed_target_types=[],
            timeout_seconds=3,
            error_codes=["invalid_input"],
            idempotent=True,
            artifact_types=[],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute(self, value: CountInput) -> CountOutput:
        return CountOutput(characters=len(value.text))
```

## ToolSpec 字段

| 字段组 | 字段 | 说明 |
| --- | --- | --- |
| 身份 | `id`、`namespace`、`name`、`display_name`、`version` | ID 由命名空间和名称固化为 `namespace.name`；版本必须为语义化版本。 |
| 来源 | `author`、`source`、`source_type` | 标记内置、Python 插件或 MCP 来源，便于审计和命名隔离。 |
| 适用性 | `description`、`capabilities`、`scenarios` | 供工具选择、前端展示和模型工具筛选使用。 |
| 安全 | `risk`、`permissions`、`requires_network`、`allowed_target_types` | 工具不能自行扩张这些声明；网络工具必须含 `network:*` 权限。 |
| 执行 | `timeout_seconds`、`error_codes`、`idempotent`、`supports_cancellation`、`supports_progress` | 由执行器和策略层使用，非幂等调用不会在结果未知时自动重试。 |
| 数据 | `artifact_types`、`input_schema`、`output_schema`、`config_schema` | 全部 JSON Schema 必须可序列化且对象拒绝额外字段。 |
| 兼容与状态 | `min_platform_version`、`max_platform_version`、`enabled`、`health` | 包声明的兼容范围和当前健康信息。 |

## 风险、网络与沙箱

工具还可声明 `consumes`、`produces`、`prerequisites`、`enables` 与 `fallback_capabilities`，用于按场景、能力和已有 Artifact 生成候选清单；这些元数据不替代 Agent 的最终决策，字段均向后兼容。

- `low`：只读、受限文本或当前 Thread 的 Artifact 处理，可使用受控进程内运行时。
- `medium`：有明确授权目标的网络或外部二进制工具，必须经过用户确认；外部二进制只允许 `SandboxRuntime`。
- `high`：策略默认拒绝。不要把高风险能力包装成低风险工具。
- `requires_network=True` 时必须声明精确网络权限和允许目标类型；不能让工具从参数接收任意 URL、命令或代理配置。
- Docker 不可用时，沙箱工具应显示不可用，绝不能退化为宿主机执行。

## Artifact、进度、取消与超时

CTF 文件工具只接收 `artifact_id`，通过 `ArtifactAccess` 在当前 Run 和 Thread 范围内读取。不要接收宿主机路径，也不要返回或伪造 `storage_ref`。纯文本 `encoding_decode` 是例外：它可接收不超过 12,000 字符的当前消息文本，且与 `artifact_id` 严格二选一；长结果必须通过 `ArtifactAccess.create_for_run` 写入当前 Run。派生文件使用 `ArtifactAccess.create`，由服务端生成安全引用和 SHA-256。

长任务在循环边界主动检查取消；收到 `asyncio.CancelledError` 时直接重新抛出。工具可调用 `await self.report_progress(50, "正在解析")` 上报 0 至 100 的进度。Agent 会把它持久化为 `tool_progress` 事件；未提供观察者的直接测试调用会安全忽略进度。

现有只读 Artifact 分析工具包括 IOC 提取与规范化、内容定位、结构化源码危险模式分析、二进制静态元数据分析、多层编码解码、文件签名/熵/字符串分析。它们均只接收 `artifact_id`，经 `ArtifactAccess` 校验当前 Run 和 Thread；不接收文件路径、不加载或执行二进制，也不会输出认证头、Token、密码或其他敏感原文。

`ToolExecutor` 以 `timeout_seconds` 或 Run 的步骤超时包裹每次调用。超时会返回结构化 `timeout` 错误，普通异常会转换为 `execution_error`，均不能使 API 或 Agent 主循环崩溃。

## 注册、MCP 与测试

当前只读 Artifact 分析还包括接口文档分析和 HTTP 响应证据分析：前者解析 OpenAPI/Swagger、Postman、JSON 或 curl 文本，后者只读取已有 HTTP evidence，不跟随链接、不执行脚本、不提交表单。

内置工具在装配层显式 `registry.register(tool)`。第三方包使用如下入口点，并由管理员在设置中心显式启用：

```toml
[project.entry-points."yuwang.tools"]
character_count = "my_package.tool:create_tool"
```

MCP 不通过 Python entry point 注册。请阅读 [MCP 集成](mcp-integration.md)，由管理员以受控 Stdio 或 Streamable HTTP 配置添加，MCP 返回的 Schema 和输出同样是不可信数据。

每个工具 PR 至少包含：输入拒绝额外字段、正常输出、异常隔离、超时、权限/风险、Artifact 范围和回归测试。可复用：

```python
await assert_tool_execution_contract(CharacterCountTool(), {"text": "hello"})
await assert_executor_boundary_contracts()
```

禁止事项：任意 Shell、Web 上传代码、任意路径读取、公开网扫描、漏洞利用、明文密钥、虚构成功结果，以及把测试替身放入生产包。
