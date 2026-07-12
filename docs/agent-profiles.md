# Agent 配置与版本

`AgentProfile` 是 v0.3 的可版本化运行配置。每个稳定 `profile_id` 下按递增 `version` 保存不可变历史，编辑、启停、设为默认和回滚都会创建新版本，不覆盖旧数据。Thread 在创建时绑定一个明确版本，Run 再保存完整配置快照，因此后续编辑不会改变历史运行、重试或恢复语义。

配置包含名称、说明、运行模式、默认与备用 Provider、用户提示词、规划策略、预算、上下文策略、记忆策略、完成模式、验证策略、人工介入规则、声明式工作流和报告模板。工作流只能引用平台注册的节点名称，Web 或导入文件不能上传 Python、JavaScript 或其他可执行代码。

## 提示词安全层

提示词按以下顺序组合：

1. 不可覆盖的安全层：授权、默认拒绝、凭据、审计和不可信输入规则。
2. 平台层：结构化输出、预算、检查点和隐藏思维链规则。
3. AgentProfile 的用户模板层。

模板只允许白名单变量：`task`、`scenario`、`thread_summary`、`current_plan`、`observations`、`remaining_budget`。解析器拒绝属性访问、索引、格式表达式、类型转换、未知变量和超长输出，不使用 `eval`、Python 表达式或任意代码执行。保存前可调用管理端模板预览接口。

## 导入导出

导出格式带 `schema_version`，只包含 Agent 配置字段。Provider 引用会清空，不包含 API Key、管理员凭据、密文、内部路径、Thread、Run、附件或审计数据。导入采用严格 Schema 校验，并为每个配置分配新的身份；不支持的 Schema 版本会明确拒绝。

## v0.2 兼容迁移

SQLite 启动迁移新增 `agent_profile_versions` 与 `run_agent_profiles`，保留原表和 JSON 数据。缺少配置引用的 v0.2 Thread 会在首次使用时绑定自动创建的“默认安全 Agent”；旧 Run 的 TaskSpec 和 Provider 快照保持原样。迁移记录只追加，不修改已提交的历史配置或运行快照。
