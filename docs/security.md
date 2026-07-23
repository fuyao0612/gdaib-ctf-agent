# 安全边界

- 所有工具必须显式注册；不存在任意 Shell 执行通道。
- 网络目标默认拒绝。参考 HTTP 工具仅允许配置中的 localhost/测试容器，且任务授权范围存在时必须再次匹配。
- 上传文件名必须是单一安全 basename，限制 5 MiB、每 Thread 8 个及允许的扩展名；服务保存 SHA-256、MIME 和不透明相对引用，API 不返回绝对路径。
- 当前阶段不解压归档文件，从根源避免 Zip Slip 与解压膨胀；未来实现必须同时限制成员路径、总大小和压缩比。
- API 限制请求体与 CORS；统一错误不暴露堆栈、密钥或内部路径。
- 事件和报告递归脱敏 `api_key/token/password/secret` 及常见 `sk-` 凭据。
- Docker 容器不挂载 Docker Socket、不使用 privileged，启用 `no-new-privileges` 并丢弃不需要的 capabilities。
- UI 只展示公开决策摘要、证据和行动原因，不收集或展示模型隐藏 CoT。

任何真实安全目标都必须由用户拥有或获得明确授权。本阶段不包含漏洞利用、Nmap/SQLMap 或公网扫描。

## 密钥与管理面

管理 API 使用仅限本机的短生命周期 HttpOnly Cookie 会话；写操作必须携带内存中的 CSRF 令牌。Provider API Key 只从管理面录入，以 `YUWANG_MASTER_KEY` 驱动的 Fernet 认证加密密文保存；公共 Provider API 只返回脱敏视图。本机工作台不应直接暴露到公网，公网部署必须由反向代理或外部身份层保护。主密钥和 Provider 密钥不得写入 Git、镜像、事件、报告或浏览器持久存储。

真实 Provider 默认只允许 HTTPS、验证服务端证书且禁止重定向；不安全 localhost 端点只能由测试进程显式开启。生产 API 镜像只复制后端运行代码，不包含测试目录或前端源文件。
