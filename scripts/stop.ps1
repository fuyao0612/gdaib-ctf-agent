# 只停止当前 Compose 项目，或经过“PID + 启动时间 + 项目路径”验证的本地开发进程。

[CmdletBinding()]
param(
    [switch]$Development,
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'process-record.ps1')
$processFile = Join-Path $ProjectRoot 'data\dev-processes.json'
$stoppedAnything = $false

if (Test-Path -LiteralPath $processFile) {
    $record = Read-YuwangProcessRecord $processFile $ProjectRoot
    $verified = @()
    $unverifiedAlive = @()
    foreach ($item in @($record.processes)) {
        $process = Get-YuwangVerifiedProcess $item $ProjectRoot
        if ($process) {
            $verified += $process
        } elseif (Get-Process -Id ([int]$item.pid) -ErrorAction SilentlyContinue) {
            $unverifiedAlive += [int]$item.pid
        }
    }
    if ($unverifiedAlive.Count -gt 0) {
        throw "PID $($unverifiedAlive -join '、') 的启动时间不匹配。为防止误杀，未停止这些进程。"
    }
    foreach ($process in $verified) {
        Stop-YuwangProcessTree $process.Id
        $stoppedAnything = $true
    }
    Remove-Item -LiteralPath $processFile -Force
    if ($verified.Count -gt 0) {
        Write-Host '已停止本项目记录的本地开发进程。' -ForegroundColor Green
    } else {
        Write-Host '本地进程记录已失效，已安全清理；没有停止其他进程。'
    }
}

if (-not $Development) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) {
        Push-Location $ProjectRoot
        try {
            $containers = @(docker compose ps -q 2>$null)
            if ($containers.Count -gt 0) {
                $logDirectory = Join-Path $ProjectRoot 'data\logs'
                [IO.Directory]::CreateDirectory($logDirectory) | Out-Null
                $previousPreference = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                try {
                    docker compose down *> (Join-Path $logDirectory 'compose-stop.log')
                    $composeExitCode = $LASTEXITCODE
                } finally {
                    $ErrorActionPreference = $previousPreference
                }
                if ($composeExitCode) {
                    throw 'Docker 服务停止失败，请查看 data\logs\compose-stop.log。'
                }
                $stoppedAnything = $true
                Write-Host '已停止当前项目的 Docker Compose 服务。' -ForegroundColor Green
            }
        } finally {
            Pop-Location
        }
    }
}

if (-not $stoppedAnything) {
    Write-Host '当前没有需要停止的御网智元服务。'
}
