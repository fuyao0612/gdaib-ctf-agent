# 黄金演示案例

三个案例只使用项目自带工具、正式 `AgentEngine` 和本机无害输入。目录中的 `manifest.yaml` 是可审查的声明式题面，`verifier/judge.yaml` 为评测侧私有配置，不能进入 Agent 提示词。

运行前请使用独立 Compose 环境和数据卷，避免修改用户现有 `data/`：

```powershell
docker compose -f compose.yaml -f compose.golden-demo.yaml up -d --build
```

打开 `http://127.0.0.1:18080`，在隔离工作台中配置并真实测试 Provider。先运行 `python scripts/init_golden_cases.py` 生成 A 的 ZIP 输入；没有 Provider 时只能运行结构和安全测试，不能把跳过写成成功。

随后可通过正式消息入口执行并生成不含答案的摘要索引：

```powershell
python scripts/run_golden_demo.py --case A-ctf-attachment --timeout 240
python scripts/run_golden_demo.py --case B-local-web --timeout 240
python scripts/run_golden_demo.py --case C-prompt-injection --timeout 240
```

案例完成后，从隔离工作台下载轨迹与报告；脚本还会调用受保护的黄金评测入口，将确定性 Judge 结果写入评测索引。清理仅限该环境：先执行 `docker compose -f compose.yaml -f compose.golden-demo.yaml down`，再执行 `docker volume rm yuwang-golden-demo_yuwang-golden-demo-data`。

| 案例 | 入口 | 确定性判定 |
| --- | --- | --- |
| A 多工具附件 CTF 闭环 | `A-ctf-attachment/manifest.yaml` | 本地 Judge 对候选值做 SHA-256 |
| B 本地 Web 动态决策 | `B-local-web/manifest.yaml` | Flag 证据与解码链绑定 |
| C Prompt Injection 安全恢复 | `C-prompt-injection/manifest.yaml` | 拒绝事件、授权快照和合法摘要 |

录屏只展示 localhost；禁止将靶场地址替换为公网目标。
