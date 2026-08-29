[CmdletBinding()]
param([string]$OutputRoot)
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($OutputRoot)) { $OutputRoot = Join-Path $PSScriptRoot '..\output\submission' }
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)

function Add-Text($slide, [string]$text, [int]$left, [int]$top, [int]$width, [int]$height, [int]$size, [bool]$bold=$false, [int]$rgb=0) {
    $shape=$slide.Shapes.AddTextbox(1,$left,$top,$width,$height)
    $shape.TextFrame.TextRange.Text=$text; $shape.TextFrame.TextRange.Font.NameFarEast='Microsoft YaHei'; $shape.TextFrame.TextRange.Font.Name='Aptos'; $shape.TextFrame.TextRange.Font.Size=$size; $shape.TextFrame.TextRange.Font.Bold=[int]$bold
    if($rgb -ne 0){$shape.TextFrame.TextRange.Font.Color.RGB=$rgb}; return $shape
}
function Add-Slide($deck,[int]$i,[string]$title,[string]$body,[string]$evidence,[string]$image='') {
    $body = $body.Replace('`r`n', [Environment]::NewLine)
    $s=$deck.Slides.Add($i,12);$s.Background.Fill.ForeColor.RGB=16711422;$s.Background.Fill.Solid()
    $bar=$s.Shapes.AddShape(1,0,0,960,18);$bar.Fill.ForeColor.RGB=10323028;$bar.Fill.Solid();$bar.Line.Visible=0
    Add-Text $s $title 60 42 840 60 29 $true 2300957|Out-Null;Add-Text $s $body 65 132 500 320 18 $false 3289650|Out-Null
    if($image -and (Test-Path $image)){$s.Shapes.AddPicture($image,$false,$true,590,126,315,360)|Out-Null}
    $line=$s.Shapes.AddShape(1,60,468,840,2);$line.Fill.ForeColor.RGB=10323028;$line.Fill.Solid();$line.Line.Visible=0
    Add-Text $s ('证据：'+$evidence) 65 485 820 35 11 $false 5526612|Out-Null;Add-Text $s ('御网智元 | 初审答辩 | '+$i+'/15') 65 525 820 20 10 $false 5526612|Out-Null
}
$desktop=Join-Path $OutputRoot '界面-桌面-20260828.png';$mobile=Join-Path $OutputRoot '界面-移动-20260828.png'
$items=@(
@('御网智元','具备自主决策能力的通用网络安全智能体`r`n初审材料 | 统一工作台 127.0.0.1:8080','技术报告；赛题要求；可运行系统',''),
@('初审回答的问题','复杂任务如何自主规划并动态调整？`r`n如何约束工具、权限与网络边界？`r`n如何让执行过程可解释、可复现、可验证？','赛题评分维度；项目证据矩阵',''),
@('可审计任务闭环','用户目标 -> 任务快照 -> 规划 -> 受控工具执行 -> 证据沉淀 -> 结果验证 -> 报告。`r`n失败、拒绝和恢复都进入同一条运行轨迹。','FastAPI；AgentEngine；SQLite；事件与报告',''),
@('不可变 Run 快照','每次运行冻结模型、Agent、授权范围、工具集合、目标和附件摘要。`r`n后续规划与结果始终关联同一 Run，避免配置漂移。','TaskSpec；Run；Artifact；ExecutionStep；Evidence',''),
@('自主规划与动态调整','模型输出结构化计划，执行器逐步落实。`r`n遇到工具失败、证据不足或安全拒绝时，系统根据状态重规划并保留原因。','规划节点；检查点；重试与重规划回归测试',''),
@('工具协同与扩展','ToolSpec 统一输入输出、风险等级、超时和审计契约。`r`n内置工具、插件和 MCP 工具通过同一注册与执行边界协同。','ToolSpec；Registry；Executor；工具契约测试',''),
@('附件理解与证据提取','附件先经过类型、大小和归档安全检查，再由受控工具解析。`r`n提取结果作为 Artifact 与 Evidence 保存，不依赖模型口头声称。','上传校验；附件解析工具；Artifact 索引',''),
@('本地 Web 只读分析','网络访问限定在用户授权范围，默认仅允许 localhost。`r`n请求、响应摘要和解析结果进入审计轨迹，越界访问被策略层拒绝。','授权范围；URL 策略；HTTP 证据工具',''),
@('Prompt Injection 防护','外部内容始终按不可信数据处理，不能覆盖系统规则与用户授权。`r`n检测到注入后记录风险并继续完成安全范围内的任务。','提示注入检测；策略拒绝；安全恢复测试',''),
@('解释、验证与复现','事件流展示每一步决策、工具参数摘要、结果和证据引用。`r`n确定性验证器负责可机器核验的结论，报告明确区分事实与推断。','trajectory；report；EvaluationScorer；验证状态',''),
@('统一工作台','任务创建、授权、执行进度、工具调用、证据与报告集中在一个界面。`r`n设置和日志均做脱敏处理，默认入口为 127.0.0.1:8080。','真实工作台；Playwright 桌面截图',$desktop),
@('移动端可用性','移动端保留任务创建、授权确认、运行状态与安全边界入口。`r`n布局针对窄屏重排，核心信息不被裁切或遮挡。','Playwright 移动端截图',$mobile),
@('工程化与供应链','Docker Compose 一键部署，提供健康检查、迁移、诊断、备份和恢复脚本。`r`n后端、前端、端到端与依赖审计纳入统一质量门禁。','full-check；健康检查；npm audit；依赖锁检查',''),
@('通用评测口径','evaluation_cases 提供可扩展的本地评测集合。`r`n统一记录任务完成度、安全性、证据充分性和可复现性，不将模型自述当作成绩。','EvaluationRunner；EvaluationScorer；测试与评测报告',''),
@('能力边界与提交','仅处理明确授权的附件和本地目标；不提供任意 Shell，不攻击公网目标，不代替外部平台提交。`r`n提交材料与可运行系统统一使用 127.0.0.1:8080。','技术报告；部署/用户手册；证据矩阵','')
)
$ppt=New-Object -ComObject PowerPoint.Application
try{$deck=$ppt.Presentations.Add();for($i=0;$i -lt $items.Count;$i++){Add-Slide $deck ($i+1) $items[$i][0] $items[$i][1] $items[$i][2] $items[$i][3]};$pptx=Join-Path $OutputRoot '御网智元-初审答辩.pptx';$pdf=Join-Path $OutputRoot '御网智元-初审答辩.pdf';$deck.SaveAs($pptx,24);$deck.SaveAs($pdf,32);$deck.Close()}finally{$ppt.Quit()}
