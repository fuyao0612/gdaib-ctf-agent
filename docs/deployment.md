# 部署、备份与故障排查

御网智元定位为单实例自托管服务，不是公网多租户 SaaS。生产环境必须在外层配置 HTTPS、身份访问控制、速率限制与定期备份；不要直接把 8080 端口暴露到公网。

## 首次部署

Windows PowerShell：

```powershell
.\yuwang.ps1 setup
.\yuwang.ps1 doctor
.\yuwang.ps1 start
```

`doctor` 只读检查 Docker、Compose、必要环境变量、端口、依赖和服务状态，不创建或重启容器。日常启动不会强制重建镜像；拉取代码更新后可运行 `.\yuwang.ps1 start -Build` 重新构建并启动。停止、状态和完整检查分别使用 `stop`、`status`、`check`。
脚本兼容 Windows PowerShell 5.1，不依赖 `RandomNumberGenerator.Fill()` 或 `Convert.ToHexString()`。已有 `.env` 时只做检查，不会重新生成或覆盖密钥。

不使用 Docker 的 Windows 开发环境可运行：

```powershell
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
Push-Location apps/web
npm ci
Pop-Location
.\yuwang.ps1 start -Development -CheckOnly
.\yuwang.ps1 start -Development
```

本地模式把数据隔离在 `data/development/`，日志写入 `data/logs/`，固定使用 8000/5173 端口，并在 `Ctrl+C` 后清理本次创建的进程。它用于开发，不替代生产 Docker 部署。

Linux/macOS：

```bash
./scripts/first-setup.sh --start
```

脚本只在 `.env` 不存在时生成高熵管理员令牌和 Fernet 主密钥，绝不覆盖已有文件，也不会把密钥打印到日志。管理员本人可在服务器本机打开 `.env`，读取 `YUWANG_ADMIN_TOKEN` 后粘贴到登录框；不要把它发送到聊天、工单或截图中。`.env` 权限应仅限服务账户，并与数据备份分开离线保管。首次打开 `http://127.0.0.1:8080` 会进入配置向导：管理员登录、添加 Provider、执行真实连接测试、确认默认 Agent，然后开始对话。Provider API Key 只在设置中心提交，并以主密钥加密后持久化。

Windows 上可运行 `notepad .env`，只复制 `YUWANG_ADMIN_TOKEN=` 等号后的完整值。若页面提示“管理员令牌不正确”，先确认没有复制变量名、引号或首尾空格；若刚手动修改 `.env`，执行 `docker compose up -d --force-recreate api` 让 API 读取新环境变量。不要在终端历史中打印令牌。

国内模型可直接选择 DeepSeek、阿里云百炼/千问或智谱 GLM 预设，核对控制台提供的 API Key 与模型名后执行“连接测试”。若使用其他 OpenAI 兼容服务，选择“自定义”，填写该服务的 HTTPS Base URL 和模型名；只有明确启用本机协议测试时才允许 HTTP localhost。

可通过 `.env` 调整 `YUWANG_WEB_PORT`、`YUWANG_DATA_PATH`、CORS、Cookie Secure 标志和 API/Web 的 CPU、内存上限。Compose 的一次性 `data-init` 服务只负责建立目录并把持久化数据交给固定 UID 10001，随后退出；长期运行的 API 仍是非 root 用户。API/Web 使用只读根文件系统、受限 tmpfs、最小 capabilities 且禁止提权。HTTPS 部署必须设置 `YUWANG_COOKIE_SECURE=true`，并把 `YUWANG_CORS_ORIGINS` 改为准确的 HTTPS 来源。

## 健康与就绪

- `/api/v1/health`：只表明进程存活并返回版本，供容器健康检查使用。
- `/api/v1/readiness`：检查数据库、主密钥、管理员配置，以及默认 Agent 是否能解析到已启用且最近真实连接测试成功的 Provider；首次配置前返回 503 是正常行为。
- `/api/v1/setup/status`：供首次配置界面读取非敏感检查结果，不返回令牌、密钥或内部路径。

## 备份、恢复和迁移

备份会短暂停止 API，保证 SQLite、附件、检查点和报告一致：

```powershell
.\scripts\backup.ps1
.\scripts\restore.ps1 -Backup .\yuwang-backup-20260712-120000.zip -Force
.\scripts\preflight.ps1
.\scripts\migrate.ps1
```

```bash
./scripts/backup.sh
./scripts/restore.sh ./yuwang-backup-20260712-120000.tgz ./data --force
./scripts/preflight.sh
./scripts/migrate.sh
```

恢复只允许项目目录内的数据路径，要求显式确认，并在恢复前停止容器。必须使用备份对应的 `YUWANG_MASTER_KEY`，否则 Provider 密文无法解密。升级顺序为：一致性备份 → `preflight` → 拉取代码/镜像 → `migrate` → 构建启动 → 检查 health/readiness → 抽查历史 Thread 与报告。应用回滚不会自动回滚数据库，必须使用升级前备份。

## 故障排查

- health 失败：检查 `docker compose ps`、API 日志、端口冲突和数据目录权限。
- readiness 的 `master_key/admin` 失败：检查 `.env` 是否完整、Fernet 格式是否有效；不要把值贴到工单或聊天。
- readiness 的 `provider` 失败：登录设置中心，启用默认 Provider 并运行连接测试。
- Provider 连接失败：根据中文错误检查鉴权、额度、模型名、HTTPS 证书和 Base URL；测试协议服务不代表真实厂商联网成功。
- 历史恢复失败：查看 `/api/v1/runs/{id}/audit`；结果不确定的非幂等调用会安全失败，不会自动重放。
- SSE 断线：浏览器使用事件序号恢复，也可通过事件查询 API 的 `after` 参数补取。
