# 架构与依赖方向

御网智元采用模块化单体。核心不导入 FastAPI、SQLite 厂商 SDK、具体模型 SDK 或具体工具实现；启动层负责注入。

```mermaid
flowchart LR
  Web["React 工作台"] -->|REST + SSE| API["FastAPI 适配层"]
  API --> Agent["AgentEngine / LangGraph"]
  API --> Repo["Repository 协议 / SQLite"]
  Agent --> Domain["稳定 Pydantic Schema"]
  Agent --> Provider["ModelProvider 协议"]
  Agent --> Tools["Tool Registry + Executor"]
  Agent --> Policy["PolicyEngine"]
  Agent --> Events["EventService"]
  Events --> Repo
  Agent --> Reports["ReportGenerator"]
  Reports --> Repo
```

## Agent 状态机

```mermaid
flowchart TD
  A["ingest"] --> B["normalize_task"] --> C["plan"] --> D["policy_check"]
  D --> E["select_tool"] --> F["execute_tool"] --> G["observe"] --> H["verify"]
  H -->|失败| I["replan"] --> E
  H -->|成功| J["complete"] --> K["generate_report"]
```

每个节点通过 `AgentStateModel` 验证输入输出并写 SQLite 检查点。状态机只理解 `AgentAction`，不解析自然语言控制指令。进程重启时，已完成历史完整读取；未完成运行标为明确的可重试失败，避免不确定地重放副作用。

## 事件协议

`Event` 包含 UUID、Run UUID、严格递增 `sequence`、`schema_version`、类型、UTC 时间、公开摘要和脱敏 payload。数据库唯一约束 `(run_id, sequence)`。SSE 使用 `id: sequence`；浏览器自动发送 `Last-Event-ID`，查询 API 也支持 `after`，因此刷新和断线不会重复。长内容进入 Artifact，事件只存摘要和引用。

同一 Thread 在数据库写入边界只允许一个 `queued/running` Run。预算在每个节点检查步骤、模型/工具调用、Token、总时长和单步超时。

## v0.2 决策与恢复语义

运行图为 `ingest → normalize_task → plan → select_action`，动作可进入策略检查/工具执行、重规划、确定性验证或安全失败。模型每次只收到任务快照、受控附件元数据、工具 Schema、公开观察和剩余预算；附件和工具输出均标记为不可信数据。重复动作与连续无进展会触发重规划或终止。

每个节点写入带序号和状态版本的追加式检查点，持久化经过的时间而不是进程单调时钟。进程启动时恢复 `queued/running` Run：已完成调用不重放，结果不确定的非幂等调用直接安全失败，幂等调用可从安全节点重试。Run 同时固化不可变 TaskSpec 与加密 Provider 快照，保证恢复和重试语义一致。

成功只能由 `SuccessVerifier` 确认：模型候选必须绑定成功工具调用 UUID 和 JSON Pointer，来源值必须完全相等，并通过正则或 SHA-256 规则。工具返回成功本身不等于任务成功。
