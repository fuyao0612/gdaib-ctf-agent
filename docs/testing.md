# 测试分层

## 一条命令完整检查

```powershell
.\yuwang.ps1 check
```

该入口先运行 `scripts/check.ps1` 的后端/前端基础门禁，再检查依赖锁、生产表面、Playwright、
`docker compose config --quiet`，并在 Windows 上验收本地启动与进程清理。它不会代替下面的
Docker 镜像构建、健康检查和重启恢复命令。测试使用隔离协议服务；Compose 配置使用仅存在于
当前进程的临时高熵值，默认不需要真实 Provider API Key。

日常只改动小范围代码时，可先运行 `.\scripts\check.ps1`；提交涉及交互、部署或启动行为的改动前，再运行完整入口。

## 后端

```powershell
pytest
pytest -q -o addopts='' --cov=yuwang.agent --cov-report=term-missing --cov-fail-under=90 tests
ruff check .
mypy
python scripts/check_production_surface.py
```

`pytest` 覆盖 Provider 协议与错误分类、设置加密、配置版本与迁移、上下文裁剪与记忆、纯模型完成、人工补充、仓储并发事件序号、预算、策略、自主循环、确定性证据验证、检查点恢复、非幂等不确定调用拒绝恢复和 FastAPI 集成。全量后端覆盖率必须不低于 85%，Agent 核心必须不低于 90%。

### 真实 Provider 验收

真实厂商冒烟测试默认不执行：没有设置 `YUWANG_RUN_REAL_PROVIDER_TEST=1` 时，pytest 会报告
“未显式启用真实 Provider 冒烟测试”；启用后仍缺少地址、密钥或模型时，会报告“未配置真实
Provider 环境变量”。这两种情况都是**跳过/未执行**，不是通过，也不应在 PR 中写成真实厂商
验收通过。只有明确准备隔离测试账户后才运行：

```powershell
$env:YUWANG_RUN_REAL_PROVIDER_TEST='1'
$env:YUWANG_REAL_PROVIDER_BASE_URL='https://approved.example/v1'
$env:YUWANG_REAL_PROVIDER_API_KEY='仅当前进程使用的测试密钥'
$env:YUWANG_REAL_PROVIDER_MODEL='approved-model'
pytest -m real_provider tests/test_real_provider_smoke.py
```

该测试只验证一次真实连接，不记录或打印完整密钥。普通聊天、意图分派和结构化 Agent 调用的
Provider 错误仍应由常规单元/集成测试覆盖；真实密钥不得写入 `.env.example`、快照、日志或 PR。

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

Playwright 启动隔离 FastAPI、SQLite 和独立 OpenAI 兼容协议测试服务。浏览器验收应同时确认：
首次配置、自然语言流式回复、刷新后恢复、受控任务的进度/报告/审计、停止与重试，以及统一
输入在运行中可作为追加指引、在等待补充或澄清时可恢复任务、暂停时可保存后续指引。对应的
低层契约在 `tests/test_dispatch.py`、`tests/test_api_integration.py` 和
`apps/web/src/components/MessageComposer.test.tsx` 中验证。

响应式用例固定检查 375×667、390×844、1024×768、1280×720、1366×768、1440×900、
1920×1080、2048×1152，并真实断言滚轮改变 `conversation.scrollTop`、最后消息和输入框可达、
workspace 不超出视口且页面无横向溢出。协议服务只位于 `tests/`，不会进入生产镜像，也不能作为
真实厂商测试证据。

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
curl http://127.0.0.1:8080/api/v1/health
docker compose restart
curl http://127.0.0.1:8080/api/v1/health
```

执行这组命令前确认默认端口未被无关程序占用；若被占用，使用隔离端口完成验收，不要结束他人
进程。重启前后应能查询同一 Thread、Run、Event、报告和检查点。验收还会检查生产源码和镜像
不含测试替身，API 镜像不包含 `tests/` 与前端源码。
