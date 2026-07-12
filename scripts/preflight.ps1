$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env'
if (-not (Test-Path -LiteralPath $envFile)) { throw 'Missing .env; run scripts/first-setup.ps1 first.' }
$raw = Get-Content -Raw -LiteralPath $envFile
foreach ($name in 'YUWANG_ADMIN_TOKEN','YUWANG_MASTER_KEY') {
    if ($raw -notmatch "(?m)^$name=(?!<)[^\s]+$") { throw "$name is missing or still a placeholder." }
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'docker was not found.' }
Push-Location $root
try { docker compose config --quiet; if ($LASTEXITCODE) { throw 'Invalid Compose configuration.' } }
finally { Pop-Location }
Write-Host 'Preflight passed. No secrets were printed.'
