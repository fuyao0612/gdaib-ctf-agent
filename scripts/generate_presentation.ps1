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
@('御网智元','可审计的通用网络安全智能体`r`n初审冲刺材料 | 仅授权附件与 localhost','技术报告；官方赛题资料（2026-08-28）',''),@('初审回答的问题','复杂任务如何自主规划？`r`n如何约束工具与授权？`r`n如何证明成功不是模型自述？','官方五维评分口径；差距矩阵',''),@('可审计闭环','正式 HTTP 消息 -> Run 快照 -> AgentEngine -> 受控工具 -> Artifact/事件 -> 独立 Judge','FastAPI、SQLite、ToolSpec/Registry/Executor',''),@('不可变 Run 快照','冻结 Provider、Agent、授权、工具、目标和附件。`r`n轨迹、报告与评测索引关联同一 Run。','TaskSpec、Run、Artifact、ExecutionStep、Evidence',''),@('黄金案例绑定','case_id + case_version + 目标/请求/附件 SHA-256 + 授权摘要 + 工具快照摘要。`r`n相似 Run 不匹配即拒绝评分。','GoldenCaseBinding；未绑定 Run 拒绝回归',''),@('案例 A：最终验证闭环','Agent 必须真实调用 FlagCandidateVerifyTool。`r`n未调用即拒绝完成并回到重规划；Judge 核验候选哈希与工具结果。','A manifest；AgentEngine；私有 Judge',''),@('案例 B：本地 Web 线索','仅允许 localhost 与冻结 URL 范围。`r`n按动态线索选择受控 HTTP 证据和解析工具。','B manifest；本地 Web lab；解码链证据',''),@('案例 C：注入后安全恢复','先识别并拒绝注入影响；再用附件检查工具完成授权摘要。`r`n不补写 safety.recovered，摘要绑定真实工具证据。','C manifest；注入安全回归',''),@('解释与复现','公开事件、参数摘要、Artifact 引用和 Judge 理由组成可追溯路径。`r`nJudge 配置不进入 Agent Prompt。','trajectory / report / evaluation index',''),@('工具协同与扩展','ToolSpec/Registry/Executor 统一输入、风险、超时和审计契约。`r`n内置、插件与 MCP 遵循同一边界。','工具契约测试；风险审批策略',''),@('上传安全与工作台','CSV、ZIP、TAR 均有条目、展开量、压缩比和解析复杂度限额。`r`n真实工作台显示脱敏状态，不含密钥。','拒绝测试；Playwright 桌面截图',$desktop),@('移动端可用性','移动端保持场景选择、授权前置与安全边界入口；不以占位页替代实际界面。','Playwright 移动端截图',$mobile),@('工程与供应链','Docker 全新构建、健康检查、隔离黄金卷、启停诊断脚本。`r`n生产与完整 npm audit 均为 0 漏洞。','full-check；audit JSON；undici 7.29.0',''),@('真实评测口径','默认卷 127.0.0.1:8080 已真实调用 Provider：A 2/3，B 3/3；C 修复后 9/9。`r`n独立黄金卷尚未配置 Provider，不将默认卷结果冒充独立卷验收。','脱敏 Run 摘要；测试与评测报告',''),@('边界与提交','不做任意 Shell、公网攻击、外部平台提交或伪造成绩。`r`n独立黄金卷需配置真实 Provider 后按同一证据链复测。','证据矩阵；部署/用户手册；签章占位','')
)
$ppt=New-Object -ComObject PowerPoint.Application
try{$deck=$ppt.Presentations.Add();for($i=0;$i -lt $items.Count;$i++){Add-Slide $deck ($i+1) $items[$i][0] $items[$i][1] $items[$i][2] $items[$i][3]};$pptx=Join-Path $OutputRoot '御网智元-初审答辩.pptx';$pdf=Join-Path $OutputRoot '御网智元-初审答辩.pdf';$deck.SaveAs($pptx,24);$deck.SaveAs($pdf,32);$deck.Close()}finally{$ppt.Quit()}
