# 扩展开发

扩展必须通过协议、注册表和 Pydantic Schema 注入，不得修改 Agent 状态机来硬编码厂商或能力。核心只依赖 Planner、ActionSelector、ContextBuilder、Memory、Verifier、ReportRenderer、WorkflowNode、ModelProvider、Repository 和 ToolExecutor 协议。

## 新增 Provider

实现 `ModelProvider` 的结构化生成接口，返回统一调用结果和真实 usage，并把鉴权、限流、超时、拒答、非法输出和服务错误映射到标准类别。在启动组合层注册能力描述和预设；不要在核心工作流写厂商判断。契约测试必须覆盖输出协商、取消、有限重试、fallback 类别和敏感数据脱敏。详见[模型 Provider](model-provider.md)。

## 新增工作流组件

实现对应协议并注册稳定名称，在 AgentProfile 的声明式 `workflow.nodes` 中引用。节点输入输出必须是 Pydantic 对象；配置只能选择已注册节点，Web 端不能上传 Python、表达式或任意可执行代码。关键安全节点、授权检查、验证和报告不能被用户配置绕过。

## 新增工具

工具声明版本、能力、输入输出 Schema、权限、风险、目标类型、网络需求、超时、幂等性和 Artifact 类型。只注册受控实现，不接受模型提供的 Shell。新增工具不应修改 Agent 状态机，详见[工具开发](tool-development.md)。

每个扩展都要提供单元契约、跨模块集成测试和失败边界测试，并保证生产镜像不包含测试 Fake/Stub。
