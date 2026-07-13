# 御网智元 v0.3.0

御网智元是一个可审计、可恢复、默认安全的高度可配置 Agent 工作台。v0.3 支持 DeepSeek、阿里云百炼/千问、智谱 GLM 与自定义 OpenAI 兼容服务，提供版本化 Agent 配置、动态工作流、上下文与记忆、纯模型任务、人工补充、预算计量和完整调用审计。

系统只执行显式注册且经过策略检查的工具，不提供任意 Shell 或默认公网扫描能力。模型输出只是候选决策和候选证据，不能直接宣告任务成功。

## Docker 启动

```powershell
.\scripts\first-setup.ps1 -Start
```

脚本只在 `.env` 缺失时生成密钥，且不会输出或覆盖密钥。也可检查配置并手工启动：

```powershell
docker compose up --build -d
curl http://localhost:8080/api/v1/health
```

打开 <http://localhost:8080>。首次使用会自动进入配置向导，依次完成管理员登录、Provider 配置与连接测试、默认 Agent 确认。API Key 只以认证加密密文持久化；主密钥和管理员令牌不进入仓库或浏览器存储。

## 使用流程

1. 在设置中心配置 Provider 与可版本化 Agent，可预览模板、导入导出、比较版本和回滚。
2. 创建任务时选择 AgentProfile；上传的安全文本附件可按预算进入不可信上下文。
3. 根据 Agent 选择建议回答、结构化输出或证据验证；只有证据模式要求确定性验证规则。
4. 通过 SSE 实时查看计划、策略判断、工具调用、重规划和验证事件。
5. 可停止运行、按原始不可变 TaskSpec/Provider 快照重试，或刷新页面继续查看历史。
6. 查看并下载 Markdown/JSON 报告；通过 `/api/v1/runs/{run_id}/audit` 查询模型、工具、证据和检查点审计。

## 本地开发与验收

```powershell
python -m pip install -e ".[dev]"
pytest
pytest -q -o addopts='' --cov=yuwang.agent --cov-report=term-missing --cov-fail-under=90 tests
ruff check .
mypy

cd apps/web
npm ci
npm run lint
npm run typecheck
npm test -- --run
npm run build
npx playwright install chromium
npm run e2e
```

完整后端覆盖率门槛为 85%，Agent 核心门槛为 90%。真实厂商冒烟测试默认跳过，显式启用方式见[测试文档](docs/testing.md)。

## 目录与文档

- `apps/api`：FastAPI REST/SSE 适配层
- `apps/web`：React 工作台与设置中心
- `src/yuwang/agent`：LangGraph 自主决策与恢复核心
- `src/yuwang/model_providers`：真实 OpenAI 兼容 Provider 与 fallback
- `src/yuwang/tooling`：受控工具 SDK 和参考工具
- `src/yuwang/storage`：SQLite 持久化、检查点与审计
- [架构与恢复](docs/architecture.md)
- [模型 Provider](docs/model-provider.md)
- [Agent 配置与版本](docs/agent-profiles.md)
- [上下文、记忆与完成可信等级](docs/context-memory.md)
- [安全边界](docs/security.md)
- [测试分层](docs/testing.md)
- [生产部署与备份](docs/deployment.md)
- [设置参考](docs/settings.md)
- [扩展开发](docs/extensions.md)
- [升级指南](docs/upgrade.md)
- [故障排查](docs/troubleshooting.md)
- [工具开发](docs/tool-development.md)
- [代码阅读与学习指南](docs/learning-guide.md)
- [协作规范](CONTRIBUTING.md)
