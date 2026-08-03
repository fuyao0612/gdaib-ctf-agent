# 本地任务包

本地任务包建议采用下列目录：

```text
evaluation_cases/<case-id>/
  manifest.yaml
  inputs/
  verifier/
  environment/
```

清单记录 case ID、版本、场景、目标、授权范围、输入资料、允许工具、预算、超时、尝试次数、结果模式、criteria、Judge、标签和难度。Agent 可见输入与 Judge 私有配置必须分开保存。

当前运行器只提供本地适配器。每次尝试保存 Run ID、模型、Provider、状态、Criterion 原始结果、得分、时长、调用量、失败类别、轨迹和报告路径；默认一次尝试，可扩展为 pass@k 统计。
