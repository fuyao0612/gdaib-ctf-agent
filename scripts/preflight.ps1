# Docker 部署前检查：验证配置和运行环境，任何输出都不得包含密钥值。

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env'

if (-not (Test-Path -LiteralPath $envFile)) {
    throw '项目缺少 .env。请先运行 .\scripts\first-setup.ps1。'
}
$raw = Get-Content -Raw -LiteralPath $envFile
foreach ($name in 'YUWANG_ADMIN_TOKEN','YUWANG_MASTER_KEY') {
    # Windows CRLF 会在行尾留下 \r，只允许行尾 \r，不允许值中出现空白。
    if ($raw -notmatch "(?m)^$name=(?!<)[^\r\n]+\r?$") {
        throw "$name 缺失或仍是占位值。请安全填写后重试，不要把值发到聊天或日志。"
    }
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw '未找到 Docker。请安装并启动 Docker Desktop，然后重新打开 PowerShell。'
}
docker info *> $null
if ($LASTEXITCODE) {
    throw 'Docker 服务未运行。请启动 Docker Desktop，等待状态正常后重试。'
}
docker compose version *> $null
if ($LASTEXITCODE) {
    throw '未找到 Docker Compose v2。请更新 Docker Desktop。'
}
Push-Location $root
try {
    docker compose config --quiet
    if ($LASTEXITCODE) {
        throw 'compose.yaml 或 .env 配置无效，请运行 docker compose config 查看具体错误。'
    }
} finally {
    Pop-Location
}
Write-Host 'Docker 启动前检查通过，未输出任何密钥。' -ForegroundColor Green
