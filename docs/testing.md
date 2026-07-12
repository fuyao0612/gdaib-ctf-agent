# 测试分层

## 后端

```powershell
pytest
pytest -q -o addopts='' --cov=yuwang.agent --cov-report=term-missing --cov-fail-under=90 tests
ruff check .
mypy
python scripts/check_production_surface.py
```

`pytest` 覆盖 Provider 协议与错误分类、设置加密、仓储并发事件序号、预算、策略、自主循环、确定性证据验证、检查点恢复、非幂等不确定调用拒绝恢复和 FastAPI 集成。全量后端覆盖率必须不低于 80%，Agent 核心必须不低于 90%。

真实厂商冒烟测试默认跳过。只有明确准备隔离测试账户后才运行：

```powershell
$env:YUWANG_RUN_REAL_PROVIDER_TEST='1'
$env:YUWANG_REAL_PROVIDER_BASE_URL='https://approved.example/v1'
$env:YUWANG_REAL_PROVIDER_API_KEY='仅当前进程使用的测试密钥'
$env:YUWANG_REAL_PROVIDER_MODEL='approved-model'
pytest -m real_provider tests/test_real_provider_smoke.py
```

## 前端与浏览器

```powershell
cd apps/web
npm ci
npm run lint
npm run typecheck
npm test -- --run
npm run build
npx playwright install chromium
npm run e2e
```

Playwright 启动隔离 FastAPI、SQLite 和独立 OpenAI 兼容协议测试服务，覆盖设置与连接测试、附件、真实生产工具、SSE、停止/重试、报告下载入口和刷新后恢复。协议服务只位于 `tests/`，不会进入生产镜像。

## Docker

```powershell
docker compose config --quiet
docker compose build
docker compose up -d
curl http://localhost:8080/api/v1/health
docker compose restart
curl http://localhost:8080/api/v1/health
```

重启前后应能查询同一 Thread、Run、Event、报告和检查点。验收还会检查生产源码和镜像不含测试替身，API 镜像不包含 `tests/` 与前端源码。
