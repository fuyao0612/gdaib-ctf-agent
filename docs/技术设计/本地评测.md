# 本地评测

评测复用正式的 `AgentEngine`、SQLite、事件、Run 快照、工具策略和报告路径。它不会注入固定模型回答，也不会把未配置真实 Provider 的情况写成通过。

## 用例

内置基线包含声明式任务用例，覆盖基础问答、任务执行、上下文、恢复、Provider 生命周期、权限、CTF 和应急响应完整性验证。每个用例都声明版本、输入资料、环境、目标、授权范围、允许工具、资源预算、超时、重试上限和类型化 `EvaluationCriterion`。用例本身不包含密钥或预制成功答案；CTF 用例只要求确定性证据，非 CTF 用例包含受控 Artifact SHA-256 校验。

`evaluation_cases/development/` 与 `evaluation_cases/acceptance/` 分别保存开发回归和验收输入，二者不共享题材输入。任务包的通过条件必须同时检查具体结构化结果的私有 Judge、Evidence 来源、必要工具或工具顺序、授权范围和预算；`result_exists`、非空文本或模型自述均不得作为任务包通过条件。验收包还覆盖无效候选、提示注入、工具失败、无进展重规划和证据收敛。Judge 私有配置不会进入 Agent 提示、TaskSpec 或普通事件。

先只查看清单，不会产生模型调用：

```powershell
python -m yuwang.evaluation list
```

## 执行

评测默认不会自动执行。执行前须在设置中心保存并测试 Provider，随后显式传入其 ID。密钥继续只保存在已加密的数据库配置中，命令不会打印密钥。

```powershell
# 只运行一个用例，默认一次尝试
python -m yuwang.evaluation run --case task-explicit-request --provider-id <Provider UUID>

# 运行日常 smoke 子集，可按需增加重复次数
python -m yuwang.evaluation run --smoke --attempts 3 --provider-id <Provider UUID>
```

### 中断后恢复

每次 `run` 会在数据库同目录写入 `*.evaluation-progress.json`。它只包含 Provider ID、选中的用例
ID、尝试次数、已完成尝试的状态和评测记录 ID；不会保存 API Key、模型回复、工具输出或 Flag。每个尝试
完成并落库后都会原子更新该文件，因此终端中断、进程退出或遇到失败时可以只继续未完成的尝试：

```powershell
python -m yuwang.evaluation run --smoke --attempts 3 --provider-id <Provider UUID> --resume

# 为并行或隔离运行使用明确的进度文件
python -m yuwang.evaluation run --smoke --provider-id <Provider UUID> --progress-file data/smoke-progress.json
python -m yuwang.evaluation run --smoke --provider-id <Provider UUID> --progress-file data/smoke-progress.json --resume
```

恢复时会严格核对 Provider、用例集合、尝试次数和已保存的评测记录。任一项不一致或记录已被删除时命令会拒绝
跳过，避免误把旧结果混入新的评测批次。

可以用 `--database` 与 `--artifacts` 指定隔离目录。没有 `YUWANG_MASTER_KEY`、Provider 不存在、未启用或未通过连接测试时，命令会明确拒绝执行，不会降级到测试替身或其他模型。

默认 Docker 部署不需要把主密钥导入 PowerShell。使用已运行 API 容器中的受控环境，并显式指向持久化目录：

```powershell
docker compose exec api python -m yuwang.evaluation run --smoke --attempts 1 --provider-id <Provider UUID> --database /data/yuwang.db --artifacts /data/artifacts
```

所有用例均经正式消息、Run、Provider 快照、Agent 循环、事件和结果持久化执行；没有普通聊天、
意图分派或直接文本 Provider 路径。无工具任务可完成，但必须保持 `validation_status=unverified`；
评分由独立 `EvaluationScorer` 完成，只读取持久化状态、事件、工具调用、快照和 EvidenceRecord；Agent 的最终总结不能直接成为成功判定。未支持的验证器标记为配置错误或未执行，绝不计为成功。Provider 故障的真实验收
只记录真实发生的调用结果，不人为伪造备用切换。

`tool_sequence` 用于验证成功工具调用的必要顺序，`evidence_source_tool` 要求结果或证据关联到指定成功调用，`authorization_scope` 阻止调用范围超出 TaskSpec，`budget_respected` 检查调用数与累计工具耗时。这些验证只读取审计事实，不接受模型说明替代。

## 结果与回放

每一次尝试都会持久化以下数据：用例 ID、分类、难度、Provider、模型、尝试次数、起止时间、耗时、模型/工具调用次数、输入/输出 Token、费用估算、步骤得分、评分器输出、成功状态、候选结果状态、结束原因、失败分类、Run ID、事件轨迹与报告引用。新的核心结果使用通用 `TaskResult`；历史 `submitted_flag` 只作为兼容字段读取。

事件和报告不被复制到评测表。结果中的 `trace_path`、`report_path` 指向正式 Run API，因此仍通过现有会话、脱敏和审计边界读取。

已登录工作台后可以查询：

```text
GET /api/v1/evaluations?provider=<name>&category=<category>&difficulty=<difficulty>
GET /api/v1/evaluations/statistics?model=<model>
GET /api/v1/evaluations/{record_id}
```

支持按用例、分类、难度、Provider、模型和结果状态筛选。统计中的成功率只以实际执行的 `passed`/`failed` 为分母，`skipped` 单独统计；同时返回平均耗时、Token、费用和失败分类分布。

Flag 仅在正式 Run 的确定性验证状态为 `validated` 时标记为 `flag_verified=true`。这不代表已向 CTFd 或任何外部平台提交。
