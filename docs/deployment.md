# 生产部署、备份与故障排查

## 部署前

在独立密钥管理系统生成并保存 `YUWANG_MASTER_KEY` 和高熵 `YUWANG_ADMIN_TOKEN`，通过部署环境注入。不要把真实值写入 Compose 文件、镜像层、仓库或工单。限制 8080 入口的网络范围，在外层反向代理启用 HTTPS、访问日志脱敏、请求速率限制和身份认证。

执行 `docker compose config --quiet` 和 `docker compose build` 后再 `docker compose up -d`。用 `/api/v1/health` 检查版本，应返回 `0.2.0`。进入设置中心录入 Provider 密钥并做连接测试。

## 持久化与备份

SQLite、附件、Provider 密文、检查点和报告都位于 `yuwang-data` 卷。备份时先暂停写入并复制整个 `/data`，确保数据库与附件一致：

```powershell
docker compose stop api
docker run --rm -v yuwang_yuwang-data:/data -v ${PWD}:/backup alpine tar czf /backup/yuwang-data.tgz -C /data .
docker compose start api
```

恢复时先停服务，把备份解压到空卷，再使用备份时对应的 `YUWANG_MASTER_KEY` 启动。主密钥丢失时 Provider 密文无法恢复；数据备份和密钥备份必须分离保存并定期演练。

## 升级与回滚

升级前备份卷，记录镜像标签和健康状态。拉取新版本后执行构建、启动、健康检查、设置列表读取与历史报告读取。回滚应用镜像不会自动回滚数据库；必须使用升级前的一致性备份。

## 故障排查

- 健康检查失败：查看 `docker compose ps` 和 `docker compose logs api web`，确认卷可写、端口未占用。
- 设置服务返回 503：确认主密钥和管理员令牌已注入，Fernet 主密钥格式有效。
- Provider 连接失败：按错误分类检查鉴权、额度、模型名、HTTPS 证书和厂商 Base URL；系统不会跟随重定向。
- 运行恢复失败：查询 `/api/v1/runs/{id}/audit`。缺少检查点/快照或存在结果不确定的非幂等工具调用时，系统会安全失败而不是重放副作用。
- SSE 断线：浏览器会携带 `Last-Event-ID` 重连；也可用事件查询 API 的 `after` 参数补取。
