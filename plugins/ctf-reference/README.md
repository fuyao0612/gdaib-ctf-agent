# CTF 工具参考

这是安全实现参考，而不是可上传或可直接安装的代码包。真实实现位于 `src/yuwang/tooling/ctf/`，包括编码解码、文件检查、字符串提取、归档解压、Flag 候选格式验证和古典密码分析。

新增 CTF 工具必须只接收 `artifact_id`，并通过 `ArtifactAccess` 验证当前 Run 与 Thread；不得接受路径、命令、URL 或 Python 代码。读写派生文件使用 `ArtifactAccess.read` 与 `ArtifactAccess.create`，以继承大小限制、路径安全、SHA-256 和审计关联。

归档处理还需拒绝路径穿越、符号链接和设备文件，并限制文件数、总大小、压缩比与递归层数。格式匹配得到的 Flag 只能标记为候选，除非有赛题平台的外部验证证据。
