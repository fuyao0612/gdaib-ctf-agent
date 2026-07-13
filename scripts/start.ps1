[CmdletBinding()]
param([switch]$Build)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

# One entry point: create secure configuration when needed, then start services.
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
