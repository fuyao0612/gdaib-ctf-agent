# 5 分钟快速入门

本文面向第一次运行项目的 Windows 用户。Provider 指模型服务连接配置；Agent 指
规划、预算和验证等行为配置；Run 指一次具体执行。

## 1. 准备环境

推荐安装并启动 Docker Desktop。项目路径尽量不包含特殊字符，然后在项目根目录
打开 Windows PowerShell 5.1 或新版 PowerShell。

```powershell
.\yuwang.ps1 setup
.\yuwang.ps1 doctor
```

`setup` 只在 `.env` 不存在时生成管理员令牌和主密钥；已有文件绝不覆盖。
`doctor` 是只读诊断，不修改配置、不停止进程，也不会打印密钥。出现“失败”时按其
中文建议处理；只有 Docker 未安装但准备使用开发模式时，可以忽略 Docker 警告。

## 2. 启动和确认状态

```powershell
.\yuwang.ps1 start
.\yuwang.ps1 status
```

看到 Web 和 API 正常后打开 <http://localhost:8080>。首次配置前 Provider 和默认
Agent 显示未就绪是正常现象。

如果没有 Docker，但已安装 Python 3.11+、Node.js 20+ 和依赖：

```powershell
.\yuwang.ps1 start -Development
```

开发页面是 <http://localhost:5173>。

## 3. 登录设置中心

用记事本打开项目根目录 `.env`，复制 `YUWANG_ADMIN_TOKEN=` 后的完整值，粘贴到
管理员验证框。不要复制变量名，不要把值发送到聊天、截图或日志。

浏览器只用令牌建立服务端会话，不会把它写入 localStorage。关闭会话或服务重启后
需要重新登录。

## 4. 配置真实模型

新手模式只需填写：

1. 厂商：DeepSeek、阿里云百炼/千问、智谱 GLM 或自定义兼容 API。
2. API 地址：预设会提供正式默认地址，仍应和厂商控制台核对。
3. API Key：从厂商控制台创建，不要写入 `.env` 或源码。
4. 模型：填写账户实际可用的模型名称。
5. 保存后点击“连接测试”。

连接测试会真实调用模型。成功后配置进度应依次变为“Provider 已连接”“默认 Agent
可用”“可以开始对话”。失败提示会说明是密钥、地址/模型、超时、额度还是结构化
输出问题。

推荐 Agent 来自服务端正式默认配置。需要修改预算、记忆、规划或验证时，再切换
“高级模式”；两种模式使用同一份数据。

## 5. 发起第一次对话

关闭设置中心，点击“新建任务”，选择默认 Agent 和普通模式。输入任务后发送。
证据模式需要填写一个可确定验证成功的正则表达式。

页面会显示：理解任务 → 制定计划 → 执行动作 → 验证结果 → 生成汇报。运行结束后
查看结果卡的最终答案、验证状态、证据、模型/工具调用、Token、停止原因和下一步。
详细技术事件在“运行审计”中。

刷新页面后，工作台会恢复最近打开的对话、Run、历史事件和报告。运行需要信息时，
按“等待用户补充”卡片在输入区补充，Agent 会从检查点继续。

## 6. 停止和排障

```powershell
.\yuwang.ps1 stop
.\yuwang.ps1 doctor
.\yuwang.ps1 help
```

`stop` 只停止当前 Compose 项目；开发模式使用：

```powershell
.\yuwang.ps1 stop -Development
```

它只停止进程记录中 PID、启动时间和项目根都匹配的进程，拒绝处理复用 PID。

常见问题：

- 打不开页面：先运行 `status`，再看 `doctor` 的端口和 Docker 项。
- 管理员失败：复制的是管理员令牌，不是 Provider API Key；修改 `.env` 后需重启。
- 401/403：Provider API Key 错误或缺权限。
- 404：API 地址或模型名称不匹配。
- 结构化 JSON 失败：核对厂商预设，必要时在高级模式切换兼容模式。

更多信息见 [故障排查](troubleshooting.md) 和 [部署文档](deployment.md)。
