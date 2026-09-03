# 报告与交接

`ReportGenerator` 是只读层：新 Run 的 TaskResult 必须先持久化，报告不会反向创建结果。报告会展示全部 TaskResult、状态、证据、步骤、失败路径、预算与复盘；非 CTF 报告不使用 Flag 文案。

交接摘要包括当前目标、授权范围、完成步骤、已验证/部分验证/未验证结果、关键证据、失败路径、阻塞、待审批、剩余预算、Provider、检查点与建议动作。可以下载 Markdown、JSON 与轨迹。
