[CmdletBinding()]
param([switch]$Build)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

# 单一启动入口：缺少配置时安全生成，已有配置时原样保留，再等待服务健康。
& (Join-Path $PSScriptRoot 'first-setup.ps1')

Write-Host 'Starting services...'
Push-Location $root
try {
    $arguments = @('compose', 'up', '-d', '--wait')
    if ($Build) { $arguments += '--build' }
    & docker $arguments
    if ($LASTEXITCODE) { throw 'Docker Compose failed. Run docker compose logs to inspect the logs.' }
} finally {
    Pop-Location
}

Write-Host ''
Write-Host 'Startup complete:' -ForegroundColor Green
Write-Host '  Open:    http://localhost:8080'
Write-Host '  Rebuild: .\scripts\start.ps1 -Build'
Write-Host '  Status:  docker compose ps'
Write-Host '  Logs:    docker compose logs -f'
Write-Host '  Stop:    docker compose down'
