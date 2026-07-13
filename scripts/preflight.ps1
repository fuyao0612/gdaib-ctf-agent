$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env'
if (-not (Test-Path -LiteralPath $envFile)) { throw 'Missing .env. Run .\scripts\first-setup.ps1 first.' }
$raw = Get-Content -Raw -LiteralPath $envFile
foreach ($name in 'YUWANG_ADMIN_TOKEN','YUWANG_MASTER_KEY') {
    # Accept both Windows (CRLF) and Unix (LF) line endings.
    if ($raw -notmatch "(?m)^$name=(?!<)\S+\r?$") { throw "$name is missing or still a placeholder." }
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker was not found. Install and start Docker Desktop, then retry.'
}
docker info *> $null
if ($LASTEXITCODE) { throw 'Docker is not running. Start Docker Desktop, then retry.' }
docker compose version *> $null
if ($LASTEXITCODE) { throw 'Docker Compose v2 was not found. Update Docker Desktop.' }
Push-Location $root
try { docker compose config --quiet; if ($LASTEXITCODE) { throw 'Invalid Docker Compose configuration.' } }
finally { Pop-Location }
Write-Host 'Preflight passed. No secrets were printed.'
