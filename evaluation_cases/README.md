# 受控本地样例

这些资料仅用于本地离线评测。它们是文本或无害源码，不包含可执行攻击载荷；逆向样例不得执行，ZIP 等归档资料也只读取清单。

`development/` 放置日常开发回归资料，`acceptance/` 放置与开发输入隔离的验收资料。两者都使用同一套声明式
manifest、私有 Judge、Artifact、Evidence、授权和预算契约；Agent 核心不读取 Judge 私有答案，也不存在按 case ID
选择行为的分支。根目录保留原有任务包以兼容既有命令和数据；验收目录还包含多层编码、多 Artifact 关联、复杂 IOC
和 localhost Web 受控分析任务。

每个任务包的通过条件至少包含具体结果的私有 Judge、来源工具 Evidence、必要工具调用或调用顺序、授权范围和预算。
`result_exists`、非空文本或模型自述不得作为任务包通过条件。
