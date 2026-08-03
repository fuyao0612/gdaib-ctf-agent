# 验证器

验证器运行在服务端或评测侧，不在 Agent 上下文中运行。`EvaluationScorer` 只读取 SQLite 中已持久化的 Run、结果、Artifact、工具调用和证据。

本地 Judge 支持 `exact_hash`、`structured_value`、`file_hash` 和 `platform_result`。期望答案与哈希保留在评测私有配置中，不进入任务说明、附件预览、普通事件、浏览器响应或 Agent 日志。Judge 执行后由服务端创建 `local_judge` EvidenceRecord。

新增验证器时，实现 `CriterionValidator`，注册到 `ValidatorRegistry`，并为通过、错误值、缺失配置和异常路径添加测试。`not_executed` 与 `configuration_error` 会原样保存在 Criterion 结果中，required 条件出现它们不能判定总成功。
