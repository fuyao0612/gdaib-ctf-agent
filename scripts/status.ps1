# 简洁项目状态：只读取运行服务的公开健康接口，不读取或输出密钥。

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'operations-common.ps1')

$values = Get-YuwangEnvValues $ProjectRoot
$webPort = Get-YuwangWebPort $values
$dockerBase = "http://127.0.0.1:$webPort"
$developmentBase = 'http://127.0.0.1:8000'
$dockerHealth = Invoke-YuwangEndpoint "$dockerBase/api/v1/health"
$developmentHealth = Invoke-YuwangEndpoint "$developmentBase/api/v1/health"

$mode = '未运行'
$webAddress = "http://127.0.0.1:$webPort"
$apiBase = $null
if ($dockerHealth.StatusCode -eq 200) {
    $mode = 'Docker'
    $apiBase = $dockerBase
} elseif ($developmentHealth.StatusCode -eq 200) {
    $mode = '本地开发'
    $webAddress = 'http://127.0.0.1:5173'
    $apiBase = $developmentBase
}

Write-Host '御网智元状态' -ForegroundColor Cyan
Write-Host "  运行方式：$mode"
Write-Host "  Web：     $webAddress"
Write-Host "  API：     $(if ($apiBase) { "$apiBase/api/v1" } else { '未启动' })"

if (-not $apiBase) {
    Write-Host '  数据库：  未检测（API 未启动）' -ForegroundColor Yellow
    Write-Host '  Provider：未检测（API 未启动）' -ForegroundColor Yellow
    Write-Host '  默认 Agent：未检测（API 未启动）' -ForegroundColor Yellow
    Write-Host '  建议：运行 .\yuwang.ps1 start，或先运行 .\yuwang.ps1 doctor。'
    return
}

$setup = Invoke-YuwangEndpoint "$apiBase/api/v1/setup/status"
if ($setup.StatusCode -ne 200 -or -not $setup.Body) {
    Write-Host '  配置状态：API 可用，但无法读取公开配置状态。' -ForegroundColor Yellow
    Write-Host '  数据库：  未检测（配置状态不可用）' -ForegroundColor Yellow
    Write-Host '  Provider：未检测（配置状态不可用）' -ForegroundColor Yellow
    Write-Host '  默认 Agent：未检测（配置状态不可用）' -ForegroundColor Yellow
    return
}
$checks = $setup.Body.checks
$agentReady = if ($checks.PSObject.Properties.Name -contains 'agent') {
    [bool]$checks.agent
} else {
    [bool]$checks.provider
}
Write-Host "  数据库：  $(if ($checks.database) { '正常' } else { '异常' })"
Write-Host "  Provider：$(if ($checks.provider) { '已连接' } else { '未连接' })"
Write-Host "  默认 Agent：$(if ($agentReady) { '可用' } else { '未就绪' })"
Write-Host "  系统状态：$(if ($setup.configured) { '可以开始对话' } else { '需要完成首次配置' })"
