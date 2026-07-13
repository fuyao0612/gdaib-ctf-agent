# 首次部署助手：仅在缺失时生成密钥，绝不覆盖既有 .env。

[CmdletBinding()]
param([switch]$Start)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env'

if (Test-Path -LiteralPath $envFile) {
    Write-Host 'Existing .env detected; current configuration will be kept.'
} else {
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = [System.Array]::CreateInstance([byte], 48)
        $generator.GetBytes($bytes)
        $firstValue = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
        $bytes = [System.Array]::CreateInstance([byte], 32)
        $generator.GetBytes($bytes)
        $secondValue = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
    } finally {
        if ($null -ne $generator) { $generator.Dispose() }
    }
    if ([string]::IsNullOrWhiteSpace($firstValue) -or [string]::IsNullOrWhiteSpace($secondValue)) {
        throw 'System random generator returned an empty value.'
    }
    $newline = [Environment]::NewLine
    $content = "YUWANG_ADMIN_TOKEN=$firstValue${newline}YUWANG_MASTER_KEY=$secondValue${newline}YUWANG_CORS_ORIGINS=http://localhost:8080${newline}YUWANG_COOKIE_SECURE=false${newline}YUWANG_WEB_PORT=8080${newline}YUWANG_DATA_PATH=./data${newline}YUWANG_API_CPUS=1.0${newline}YUWANG_API_MEMORY=768M${newline}YUWANG_WEB_CPUS=0.5${newline}YUWANG_WEB_MEMORY=192M"
    [IO.File]::WriteAllText($envFile, $content, (New-Object Text.UTF8Encoding($false)))
    Write-Host '.env created. Secrets were not printed; store them securely offline.'
}

& (Join-Path $PSScriptRoot 'preflight.ps1')
if ($Start) {
    Write-Host 'Building and starting services. The first run may take a few minutes...'
    Push-Location $root
    try {
        docker compose up -d --build --wait
        if ($LASTEXITCODE) { throw 'Docker Compose failed. Run docker compose logs to inspect the logs.' }
    } finally {
        Pop-Location
    }
    Write-Host ''
    Write-Host 'Startup complete:' -ForegroundColor Green
    Write-Host '  Open:   http://localhost:8080'
    Write-Host '  Status: docker compose ps'
    Write-Host '  Logs:   docker compose logs -f'
    Write-Host '  Stop:   docker compose down'
}
