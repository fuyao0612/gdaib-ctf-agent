# 测试分层

## 后端

`pytest` 运行契约、仓储/事件、Provider、工具、策略、预算、LangGraph 闭环和 FastAPI 临时 SQLite 集成测试，并强制 Agent 核心覆盖率至少 80%。

```powershell
pytest
ruff check .
mypy
```

## 前端

```powershell
cd apps/web
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

## 浏览器与 Docker

```powershell
cd apps/web
npx playwright install chromium
npm run e2e
cd ../..
docker compose config --quiet
docker compose up --build -d
curl http://localhost:8080/api/v1/health
docker compose restart
```

重启后在页面或 API 查询原 Thread、Message、Run 和 Event。测试数据不能写死在前端；E2E 必须经过 FastAPI、SQLite、SSE 和报告生成。
