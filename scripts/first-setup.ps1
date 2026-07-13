# 首次部署助手：仅在缺失时生成密钥，绝不覆盖既有 .env。
[CmdletBinding()]
param([switch]$Start)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env'
if (Test-Path -LiteralPath $envFile) {
    Write-Host '.env already exists; no changes were made.'
} else {
    $adminBytes = [byte[]]::new(32)
    $masterBytes = [byte[]]::new(32)
    [Security.Cryptography.RandomNumberGenerator]::Fill($adminBytes)
    [Security.Cryptography.RandomNumberGenerator]::Fill($masterBytes)
    $admin = [Convert]::ToHexString($adminBytes).ToLowerInvariant()
    $master = [Convert]::ToBase64String($masterBytes).Replace('+','-').Replace('/','_')
    $content = @"
YUWANG_ADMIN_TOKEN=$admin
YUWANG_MASTER_KEY=$master
YUWANG_CORS_ORIGINS=http://localhost:8080
YUWANG_COOKIE_SECURE=false
YUWANG_WEB_PORT=8080
YUWANG_DATA_PATH=./data
YUWANG_API_CPUS=1.0
YUWANG_API_MEMORY=768M
YUWANG_WEB_CPUS=0.5
YUWANG_WEB_MEMORY=192M
"@
    [IO.File]::WriteAllText($envFile, $content, [Text.UTF8Encoding]::new($false))
    Write-Host '.env created. Secrets were not printed; store them securely offline.'
}
& (Join-Path $PSScriptRoot 'preflight.ps1')
if ($Start) { Push-Location $root; try { docker compose up -d --build } finally { Pop-Location } }
