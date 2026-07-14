# 御网智元唯一需要记忆的 Windows 入口；这里只调度内部脚本，不复制业务逻辑。

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup','start','stop','status','doctor','check','help')]
    [string]$Command = 'help',
    [switch]$Development,
    [switch]$Build,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$root = $PSScriptRoot

function Show-Help {
    Write-Host '御网智元统一入口' -ForegroundColor Cyan
    Write-Host '  .\yuwang.ps1 setup                 首次生成本机配置并显示依赖提示'
    Write-Host '  .\yuwang.ps1 start                 使用 Docker 启动（推荐）'
    Write-Host '  .\yuwang.ps1 start -Development    本地启动 API 与 Web'
    Write-Host '  .\yuwang.ps1 stop                  安全停止本项目服务'
    Write-Host '  .\yuwang.ps1 status                查看地址和就绪状态'
    Write-Host '  .\yuwang.ps1 doctor                执行只读中文诊断'
    Write-Host '  .\yuwang.ps1 check                 运行完整质量检查'
    Write-Host '  .\yuwang.ps1 help                  显示本帮助'
}

switch ($Command) {
    'setup' {
        & (Join-Path $root 'scripts\first-setup.ps1') -SkipPreflight
        Write-Host ''
        Write-Host '首次基础配置已准备完成。' -ForegroundColor Green
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            Write-Host '提示：推荐安装 Docker Desktop；也可安装 Python 3.11+、Node.js 20+ 后使用 -Development。' -ForegroundColor Yellow
        }
        Write-Host '下一步：运行 .\yuwang.ps1 doctor，然后运行 .\yuwang.ps1 start。'
    }
    'start' {
        $arguments = @{ Quiet = $true }
        if ($Development) { $arguments.Development = $true }
        if ($Build) { $arguments.Build = $true }
        if ($CheckOnly) { $arguments.CheckOnly = $true }
        & (Join-Path $root 'scripts\start.ps1') @arguments
    }
    'stop' {
        & (Join-Path $root 'scripts\stop.ps1') -Development:$Development
    }
    'status' { & (Join-Path $root 'scripts\status.ps1') }
    'doctor' { & (Join-Path $root 'scripts\doctor.ps1') }
    'check' { & (Join-Path $root 'scripts\full-check.ps1') }
    'help' { Show-Help }
}
