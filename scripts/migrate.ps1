# 显式运行幂等数据库迁移，并在失败时立即停止。
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
& (Join-Path $PSScriptRoot 'preflight.ps1')
Push-Location $root
try { docker compose run --rm api python -c "from apps.api.main import Settings,create_app; create_app(Settings()); print('Configuration and database migration completed')"; if ($LASTEXITCODE) { throw 'Migration failed.' } }
finally { Pop-Location }
