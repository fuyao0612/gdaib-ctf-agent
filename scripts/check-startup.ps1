# Windows 启动安全验收：真实启动开发服务，并验证密钥、端口冲突和进程清理。

[CmdletBinding()]
param(
    [switch]$SkipRuntimeSmoke
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$startScript = Join-Path $PSScriptRoot 'start.ps1'
$envFile = Join-Path $root '.env'
$processFile = Join-Path $root 'data\dev-processes.json'
$envExisted = Test-Path -LiteralPath $envFile

function Invoke-Startup([string[]]$StartupArguments) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass `
            -File $startScript @StartupArguments 2>&1 | Out-String
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return @{ ExitCode = $exitCode; Output = $output }
}

function Read-SensitiveValues {
    $values = @()
    foreach ($line in Get-Content -LiteralPath $envFile) {
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2 -and $parts[0] -in 'YUWANG_ADMIN_TOKEN','YUWANG_MASTER_KEY') {
            $values += $parts[1]
        }
    }
    return $values
}

function Test-PortOpen([int]$Port) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $result = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $result.AsyncWaitHandle.WaitOne(300)) { return $false }
        $client.EndConnect($result)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

try {
    Write-Host '[启动验收] 检查 PowerShell 5.1 解析和本地开发依赖…'
    $check = Invoke-Startup @('-Development', '-CheckOnly')
    if ($check.ExitCode -ne 0) {
        throw "本地开发启动检查失败。请直接运行 .\scripts\start.ps1 -Development -CheckOnly 查看原因。"
    }

    foreach ($value in Read-SensitiveValues) {
        if (-not [string]::IsNullOrWhiteSpace($value) -and $check.Output.Contains($value)) {
            throw '启动输出包含管理员令牌或主密钥。'
        }
    }
    Write-Host '[启动验收] 敏感值未出现在启动输出中。' -ForegroundColor Green

    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 8000)
    $listener.Start()
    try {
        $conflict = Invoke-Startup @('-Development', '-CheckOnly')
        if ($conflict.ExitCode -eq 0 -or $conflict.Output -notmatch '端口 8000 已被占用') {
            throw '端口冲突没有被启动脚本正确拒绝。'
        }
    } finally {
        $listener.Stop()
    }
    Write-Host '[启动验收] 端口冲突会给出明确中文错误。' -ForegroundColor Green

    if (-not $SkipRuntimeSmoke) {
        Write-Host '[启动验收] 真实启动 API 与 Web，并在 3 秒后自动清理…'
        $smoke = Invoke-Startup @('-Development', '-RunSeconds', '3')
        if ($smoke.ExitCode -ne 0 -or $smoke.Output -notmatch '本地开发服务启动成功') {
            throw '本地 API/Web 启动冒烟失败。请查看 data\logs 中的 stderr 日志。'
        }
        if ((Test-PortOpen 8000) -or (Test-PortOpen 5173)) {
            throw '启动验收结束后仍有 8000 或 5173 端口未释放。'
        }
        if (Test-Path -LiteralPath $processFile) {
            throw '启动验收结束后仍残留 data\dev-processes.json。'
        }
        Write-Host '[启动验收] 服务可访问，且子进程、端口和记录均已清理。' -ForegroundColor Green
    }

    Write-Host 'Windows 启动安全验收通过。' -ForegroundColor Green
} finally {
    if (-not $envExisted -and (Test-Path -LiteralPath $envFile)) {
        Remove-Item -LiteralPath $envFile -Force
    }
}
