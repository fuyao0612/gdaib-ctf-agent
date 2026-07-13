# 启动前检查环境、Compose 配置、权限和必需密钥是否满足生产条件。
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env'
if (-not (Test-Path -LiteralPath $envFile)) { throw 'Missing .env; run scripts/first-setup.ps1 first.' }
$raw = Get-Content -Raw -LiteralPath $envFile
foreach ($name in 'YUWANG_ADMIN_TOKEN','YUWANG_MASTER_KEY') {
    # Windows 的 CRLF 会把 \r 留在行尾，因此显式允许它而不放宽值中的空白。
    if ($raw -notmatch "(?m)^$name=(?!<)[^\r\n]+\r?$") { throw "$name is missing or still a placeholder." }
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'docker was not found.' }
Push-Location $root
try { docker compose config --quiet; if ($LASTEXITCODE) { throw 'Invalid Compose configuration.' } }
finally { Pop-Location }
Write-Host 'Preflight passed. No secrets were printed.'
