[CmdletBinding()]
param([switch]$Start)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env'

function New-SecureRandomBytes {
    param([Parameter(Mandatory = $true)][int]$Length)

    # RandomNumberGenerator.Fill() is unavailable in Windows PowerShell 5.1.
    $bytes = New-Object byte[] $Length
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return $bytes
}

if (Test-Path -LiteralPath $envFile) {
    Write-Host 'Existing .env detected; current configuration will be kept.'
} else {
    $adminBytes = New-SecureRandomBytes -Length 32
    $masterBytes = New-SecureRandomBytes -Length 32
    # Convert.ToHexString() is also unavailable in Windows PowerShell 5.1.
    $admin = ([BitConverter]::ToString($adminBytes) -replace '-', '').ToLowerInvariant()
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
    Write-Host '.env created. No secrets were printed.'
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
