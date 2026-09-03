# 初审材料索引

本目录只存放与比赛初审直接相关的材料，按用途分为源稿和可提交成品。

## 目录结构

- `source/`：可检索、可继续编辑的 Markdown 源稿，编号 01-18。
- `deliverables/documents/`：方案设计、技术报告、开发文档、测试报告和用户手册的 DOCX/PDF 成品。
- `deliverables/presentation/`：方案介绍 PPTX/PDF。
- `deliverables/media/`：桌面端和移动端界面截图。
- `deliverables/supporting/`：演示视频镜头表、第三方依赖与许可证清单。
- `deliverables/evidence/`：五维证据矩阵和脱敏依赖审计 JSON。

## 建议阅读顺序

1. `deliverables/presentation/御网智元-方案介绍.pdf`
2. `deliverables/documents/御网智元-技术报告.pdf`
3. `deliverables/documents/御网智元-方案设计文档.pdf`
4. `deliverables/documents/御网智元-测试与评测报告.pdf`
5. `deliverables/documents/御网智元-用户手册.pdf`
6. `deliverables/documents/御网智元-开发文档.pdf`

## 重新生成成品

在项目根目录执行：

```powershell
python scripts/generate_reports.py
.\scripts\generate_presentation.ps1
```

生成结果会自动写入本目录对应的成品子目录，不再使用临时 `output/` 目录。

## 程序验收

在 Windows PowerShell 中执行：

```powershell
.\yuwang.ps1 setup
.\yuwang.ps1 doctor
.\yuwang.ps1 start
```

打开 <http://127.0.0.1:8080>。完整质量检查使用 `.\yuwang.ps1 check`。

## 正式提交提醒

报名人与单位信息、签字、盖章和保密声明扫描件不放入公开 GitHub；请按主办方模板单独准备并在指定平台提交。
