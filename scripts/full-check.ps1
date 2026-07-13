# 完整质量入口：覆盖静态检查、单元/集成测试、构建、浏览器、生产表面和启动安全。

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$oldAdminToken = [Environment]::GetEnvironmentVariable('YUWANG_ADMIN_TOKEN', 'Process')
$oldMasterKey = [Environment]::GetEnvironmentVariable('YUWANG_MASTER_KEY', 'Process')

function New-RandomUrlSafeValue([int]$ByteCount) {
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = [Array]::CreateInstance([byte], $ByteCount)
        $generator.GetBytes($bytes)
        return [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
    } finally {
        $generator.Dispose()
    }
}

function Write-Stage([string]$Message) {
    Write-Host "`n[完整检查] $Message" -ForegroundColor Cyan
}

Push-Location $root
try {
    Write-Stage '1/5 运行后端、前端静态检查、单元测试和生产构建'
    & (Join-Path $PSScriptRoot 'check.ps1')
    python scripts/check_dependency_locks.py
    if ($LASTEXITCODE) { throw '依赖锁文件一致性检查失败。' }
    python -m pip check
    if ($LASTEXITCODE) { throw 'Python 依赖存在版本冲突。' }

    Write-Stage '2/5 检查生产代码与镜像表面不包含测试替身'
    python scripts/check_production_surface.py
    if ($LASTEXITCODE) { throw '生产表面检查失败。' }

    Write-Stage '3/5 运行 Playwright 完整浏览器测试'
    Push-Location apps/web
    try {
        npm run e2e
        if ($LASTEXITCODE) { throw 'Playwright 浏览器测试失败。' }
    } finally {
        Pop-Location
    }

    Write-Stage '4/5 验证 Docker Compose 配置（使用临时高熵值，不需要真实 API Key）'
    if ([string]::IsNullOrWhiteSpace($oldAdminToken)) {
        [Environment]::SetEnvironmentVariable(
            'YUWANG_ADMIN_TOKEN', (New-RandomUrlSafeValue 32), 'Process'
        )
    }
    if ([string]::IsNullOrWhiteSpace($oldMasterKey)) {
        [Environment]::SetEnvironmentVariable(
            'YUWANG_MASTER_KEY', (New-RandomUrlSafeValue 32), 'Process'
        )
    }
    docker compose config --quiet
    if ($LASTEXITCODE) { throw 'Docker Compose 配置检查失败。' }

    Write-Stage '5/5 运行 Windows 启动安全验收'
    if ($env:OS -eq 'Windows_NT') {
        & (Join-Path $PSScriptRoot 'check-startup.ps1')
    } else {
        Write-Host '当前不是 Windows，跳过 PowerShell 5.1 启动进程验收。'
    }

    Write-Host "`n完整检查全部通过。" -ForegroundColor Green
} finally {
    [Environment]::SetEnvironmentVariable('YUWANG_ADMIN_TOKEN', $oldAdminToken, 'Process')
    [Environment]::SetEnvironmentVariable('YUWANG_MASTER_KEY', $oldMasterKey, 'Process')
    Pop-Location
}
