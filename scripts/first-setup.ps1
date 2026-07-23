# 首次部署助手：仅在缺失时生成密钥，绝不覆盖既有 .env。

[CmdletBinding()]
param(
    [switch]$Start,
    [switch]$SkipPreflight
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env'

if (Test-Path -LiteralPath $envFile) {
    Write-Host '检测到已有 .env，将保留现有配置，不会覆盖密钥。'
} else {
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = [System.Array]::CreateInstance([byte], 32)
        $generator.GetBytes($bytes)
        $masterKey = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
    } finally {
        if ($null -ne $generator) { $generator.Dispose() }
    }
    if ([string]::IsNullOrWhiteSpace($masterKey)) {
        throw '系统随机数生成失败，未写入 .env。请重试或检查系统加密服务。'
    }
    $newline = [Environment]::NewLine
    $content = "YUWANG_MASTER_KEY=$masterKey${newline}YUWANG_CORS_ORIGINS=http://127.0.0.1:8080,http://localhost:8080${newline}YUWANG_COOKIE_SECURE=false${newline}YUWANG_WEB_PORT=8080${newline}YUWANG_DATA_PATH=./data${newline}YUWANG_API_CPUS=1.0${newline}YUWANG_API_MEMORY=768M${newline}YUWANG_WEB_CPUS=0.5${newline}YUWANG_WEB_MEMORY=192M"
    [IO.File]::WriteAllText($envFile, $content, (New-Object Text.UTF8Encoding($false)))
    Write-Host '已创建 .env。密钥未输出到终端，请在本机安全保存。'
}

if ($Start) {
    # 旧入口保留兼容，但所有检查、端口处理和输出都交给统一启动脚本。
    & (Join-Path $PSScriptRoot 'start.ps1') -Build
    return
}
if (-not $SkipPreflight) {
    & (Join-Path $PSScriptRoot 'preflight.ps1')
}
