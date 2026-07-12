# 御网智元 v0.2.0

御网智元是一个可审计、可恢复、默认安全的对话式网络安全 Agent 工作台。v0.2 接入 DeepSeek、阿里云百炼/千问、智谱 GLM 与自定义 OpenAI 兼容服务，提供结构化自主决策、确定性成功验证、断点恢复和完整调用审计。

系统只执行显式注册且经过策略检查的工具，不提供任意 Shell 或默认公网扫描能力。模型输出只是候选决策和候选证据，不能直接宣告任务成功。

## Docker 启动

```powershell
Copy-Item .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

把两条命令生成的值分别写入 `.env` 的 `YUWANG_MASTER_KEY` 和 `YUWANG_ADMIN_TOKEN`，然后启动：

```powershell
docker compose up --build -d
curl http://localhost:8080/api/v1/health
```

打开 <http://localhost:8080>。首次使用进入“设置中心”，输入管理员令牌，选择厂商预设或自定义兼容端点，录入 API Key，执行连接测试并设为默认 Provider。API Key 只以认证加密密文持久化；主密钥和管理员令牌不进入仓库。

## 使用流程

1. 在设置中心配置并测试至少一个真实 Provider，可设置默认项与 fallback 顺序。
2. 创建任务，上传受控附件，描述授权范围与目标。
3. 填写确定性成功正则，选择运行模型并启动。
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

完整后端覆盖率门槛为 80%，Agent 核心门槛为 90%。真实厂商冒烟测试默认跳过，显式启用方式见[测试文档](docs/testing.md)。

## 目录与文档

- `apps/api`：FastAPI REST/SSE 适配层
- `apps/web`：React 工作台与设置中心
- `src/yuwang/agent`：LangGraph 自主决策与恢复核心
- `src/yuwang/model_providers`：真实 OpenAI 兼容 Provider 与 fallback
- `src/yuwang/tooling`：受控工具 SDK 和参考工具
- `src/yuwang/storage`：SQLite 持久化、检查点与审计
- [架构与恢复](docs/architecture.md)
- [模型 Provider](docs/model-provider.md)
- [安全边界](docs/security.md)
- [测试分层](docs/testing.md)
- [生产部署与备份](docs/deployment.md)
- [工具开发](docs/tool-development.md)
- [协作规范](CONTRIBUTING.md)
