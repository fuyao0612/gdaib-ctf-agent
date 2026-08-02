# 通用安全 Agent 设计说明

本文记录将现有 CTF Agent 扩展为通用、可验证安全任务 Agent 时采用的最小契约。实现继续复用 FastAPI、React、SQLite、LangGraph、Tool Registry、Function Calling、MCP、审计和检查点，不引入新的服务或向量数据库。

## 任务结果与证据

每次执行仍以 `Run` 为生命周期记录，以 `TaskSpec` 固化任务输入、授权目标、预算、验证规则和工具快照。对外结果由报告事实生成器从持久化 Run、事件、工具调用、执行步骤、Artifact 和 EvidenceRecord 读取，不从 Agent 的最终文字自行推断“成功”。CTF Flag 只是 CTF 场景下的一种结果候选；普通安全分析使用同一结果和证据模型，不会出现 Flag 专属区块。

`EvaluationCriterion` 是独立评分契约。每项准则声明稳定 ID、说明、权重、验证器类型、期望值和是否必需。`EvaluationScorer` 只读取 SQLite 中已持久化的状态、事件、快照、工具调用和确定性证据；模型自述、普通非空文本和自然语言关键词不能改变评分。未支持的验证器会得到配置错误，而不是被跳过后误报通过。

## 场景适配

场景只影响任务 brief、工具选择和报告模板，不改变核心 Run、Evidence、Artifact、Checkpoint 或评分接口。内置场景包括 CTF、授权渗透测试、应急响应、漏洞分析、逆向分析和通用安全分析。所有外部目标必须显式授权，工具仍经过 PolicyEngine、风险审批和沙箱边界。

## 输入资料

Artifact 保存文件名、类型、大小、SHA-256、来源、上传时间、可信级别、提取摘要、截断状态和原始引用。资料内容按不可信数据进入上下文，Prompt Injection 只作为风险事实记录，不获得系统权限。压缩包先保存清单和风险，再由显式受控工具读取；大文件只把可解释摘要与引用放入模型上下文。

## 评测与历史兼容

类型化评测采用 `Task + Solver + EvaluationScorer + Sandbox` 的形状。旧 JSON 评测记录继续由 Pydantic 默认字段读取，旧的 `submitted_flag` 仅作为历史展示兼容字段，新的核心验证依赖 `EvidenceRecord` 和确定性规则。SQLite 迁移使用现有幂等初始化与 JSON 数据兼容路径，不删除历史 Run、CTF 报告或审计记录。

## UI 与报告

Run 页面先显示执行状态和验证状态，再显示证据、候选结果、时间线、失败路径、资源消耗、完整报告和人机交接摘要。只有 CTF 场景显示 Flag 候选；普通场景显示通用结果。历史运行按旧字段回退渲染。移动端使用独立历史滚动区和可换行的结果内容，不能以隐藏 overflow 掩盖保存按钮或表单。
