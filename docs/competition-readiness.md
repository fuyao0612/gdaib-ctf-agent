# 比赛交付清单

| 评分项 | 实现位置 | 验收证据 |
| --- | --- | --- |
| 真实结果闭环 | `src/yuwang/results` | TaskResult 服务测试、报告与 UI |
| 独立验证 | `src/yuwang/evaluation` | Local Judge 测试、Criterion 原始状态 |
| 安全边界 | `src/yuwang/policy`、`src/yuwang/tooling` | 授权、注入与工具契约测试 |
| 可恢复运行 | `src/yuwang/agent`、SQLite | 检查点和控制接口测试 |
| 部署与展示 | Compose、React | Docker 健康检查、前端 E2E 截图 |

提交前运行后端 pytest、Ruff、Mypy，前端 lint、typecheck、test、build、E2E，以及 Compose 配置、构建、启动、持久化重启检查。真实 Provider 仅在存在有效本地配置时执行，密钥绝不输出。
