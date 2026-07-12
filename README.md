# 御网智元 v0.1.0

御网智元是一个可审计、可恢复、默认安全的对话式网络安全 Agent 工作台。第一阶段用确定性 Mock 模型和受控参考工具跑通完整工程闭环，不执行真实漏洞利用、任意 Shell 或公网扫描。

## 一条命令启动

需要 Docker Desktop 与 Compose：

```bash
docker compose up --build
```

打开 <http://localhost:8080>。API 健康检查位于 <http://localhost:8080/api/v1/health>，OpenAPI 位于 <http://localhost:8080/api/docs>。首次演示不需要 API Key，数据保存在 `yuwang-data` Docker 卷中。

## 完整演示

1. 点击“创建第一个任务”，选择 `normal` 或 `competition`。
2. 上传 `.txt/.json/.md/.log/.bin` 示例文件并输入已授权任务。
3. 点击“启动运行”。Mock Provider 产生结构化 `CallTool`。
4. 首次 `mock_echo` 按演示场景失败；右侧出现工具审计卡片。
5. Agent 发出 `replanned`，第二次调用成功并验证条件。
6. 查看最终 Markdown 报告，下载 Markdown/JSON；刷新页面验证历史仍存在。
7. `competition` 模式运行中输入区会锁定，只保留观察、停止和重试。

## 架构

模块化单体保持依赖向内：React 只调用版本化 REST/SSE；FastAPI 负责传输层；Agent 核心通过协议接收 Provider、工具、策略和仓储。SQLite 持久化领域对象、检查点、严格递增事件与报告。详见 [架构文档](docs/architecture.md)。

```text
apps/api       FastAPI 传输层        apps/web       React 工作台
src/yuwang/domain                  稳定 Pydantic 契约
src/yuwang/agent                   LangGraph 状态闭环
src/yuwang/model_providers         Mock / OpenAI 兼容 Provider
src/yuwang/tooling + plugins       Tool SDK 与安全参考工具
src/yuwang/policy                  默认拒绝策略与脱敏
src/yuwang/events + storage        SSE 事件与 SQLite
src/yuwang/reports                 Markdown/JSON 报告
tests                              单元与集成测试
```

## 本地开发

```powershell
python -m pip install -e ".[dev]"
pytest
python -m uvicorn apps.api.main:app --reload

cd apps/web
npm ci
npm run dev
```

全量静态检查与测试可执行 `powershell -File scripts/check.ps1`。浏览器测试执行 `cd apps/web && npx playwright install chromium && npm run e2e`。

## 文档

- [架构与事件流](docs/architecture.md)
- [工具开发](docs/tool-development.md)
- [模型 Provider](docs/model-provider.md)
- [安全边界](docs/security.md)
- [测试分层](docs/testing.md)
- [协作规范](CONTRIBUTING.md)
