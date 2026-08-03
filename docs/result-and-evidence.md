# 结果与证据

一次 Run 的顺序固定为：Agent 输出结构化候选，`TaskResultService` 绑定当前 Run 的真实证据，独立验证器给出状态，随后写入 `Run.results`，最后生成报告。

候选只包含类型、标题、摘要、结构化数据、证据候选和置信度。模型不能填写验证状态、验证器名称或“工具已验证”等字段。证据绑定器只接受当前 Run 的 EvidenceRecord、工具调用、执行步骤及任务输入 Artifact；伪造 ID 和跨 Run ID 会被丢弃。

状态含义：`unverified` 是没有可绑定证据，`partial` 是已绑定但尚未全部验证，`validated` 是服务端独立验证通过，`failed` 是验证失败或 Run 未成功完成。报告、UI、交接摘要和评测都读取同一份 `Run.results`。
