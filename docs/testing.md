# 测试分层

## 后端

```powershell
pytest
pytest -q -o addopts='' --cov=yuwang.agent --cov-report=term-missing --cov-fail-under=90 tests
ruff check .
mypy
python scripts/check_production_surface.py
```

`pytest` 覆盖 Provider 协议与错误分类、设置加密、配置版本与迁移、上下文裁剪与记忆、纯模型完成、人工补充、仓储并发事件序号、预算、策略、自主循环、确定性证据验证、检查点恢复、非幂等不确定调用拒绝恢复和 FastAPI 集成。全量后端覆盖率必须不低于 85%，Agent 核心必须不低于 90%。

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

Playwright 启动隔离 FastAPI、SQLite 和独立 OpenAI 兼容协议测试服务，覆盖首次配置、Agent 创建与版本回滚、dynamic/direct/hybrid 三种规划策略、连续对话、建议回答、人工补充、记忆删除、配置审计、附件、真实生产工具、SSE、停止/重试、错误提示、报告下载入口和刷新自动恢复当前对话。响应式用例固定检查 1024×768、1280×720、1366×768、1440×900、1920×1080，并验证设置滚动区、长标题、长文件名、长事件内容、Composer 和窄屏审计抽屉不会产生横向溢出。协议服务只位于 `tests/`，不会进入生产镜像，也不能作为真实厂商测试证据。

## Windows 启动安全验收

```powershell
.\scripts\check-startup.ps1
```

检查会通过 PowerShell 5.1 子进程验证开发依赖和环境变量，确认敏感值不进入启动输出、端口冲突被明确拒绝，并真实启动 API/Web 三秒后验证递归清理。默认不调用 Provider，不需要真实 API Key。

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
