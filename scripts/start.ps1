# Windows 统一启动入口：默认使用 Docker，-Development 启动本地开发进程。

[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$Development,
    [switch]$CheckOnly,
    [switch]$Quiet,
    [switch]$OpenBrowser,
    [ValidateRange(0, 300)]
    [int]$RunSeconds = 0
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $root '.env'
$logDirectory = Join-Path $root 'data\logs'
$processFile = Join-Path $root 'data\dev-processes.json'
. (Join-Path $PSScriptRoot 'process-record.ps1')

function Write-Step([string]$Message) {
    Write-Host "[检查] $Message" -ForegroundColor Cyan
}

function Get-RequiredCommand([string]$Name, [string]$InstallHint) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) { throw "未找到 $Name。$InstallHint" }
    return $command
}

function Show-OptionalCommand([string]$Name, [string]$Purpose) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        Write-Host "  $Name：已安装（$Purpose）" -ForegroundColor DarkGreen
    } else {
        Write-Host "  $Name：未安装（仅 $Purpose 需要，不影响 Docker 启动）" -ForegroundColor DarkYellow
    }
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

function Read-DotEnv {
    if (-not (Test-Path -LiteralPath $envFile)) {
        throw '项目缺少 .env。请先运行 .\scripts\first-setup.ps1。'
    }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith('#')) {
            continue
        }
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2) { $values[$parts[0].Trim()] = $parts[1].Trim() }
    }
    foreach ($name in 'YUWANG_ADMIN_TOKEN','YUWANG_MASTER_KEY') {
        if (-not $values.ContainsKey($name) -or
            [string]::IsNullOrWhiteSpace($values[$name]) -or
            $values[$name].StartsWith('<')) {
            throw "$name 缺失或仍是占位值。请删除无效 .env 后重新运行首次配置脚本，或安全填写该值。"
        }
    }
    return $values
}

function Import-YuwangEnvironment([hashtable]$Values) {
    foreach ($entry in $Values.GetEnumerator()) {
        if ($entry.Key -like 'YUWANG_*') {
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
        }
    }
}

function Assert-DevelopmentDependencies {
    Write-Step '检查 Python、Node.js、npm 和本地依赖'
    $python = Get-RequiredCommand 'python' '请安装 Python 3.11 或更高版本，并重新打开 PowerShell。'
    $node = Get-RequiredCommand 'node' '请安装 Node.js 20 或更高版本，并重新打开 PowerShell。'
    $npm = Get-RequiredCommand 'npm.cmd' 'npm 随 Node.js 安装；请重新安装 Node.js。'

    $pythonVersionText = (& $python.Source --version 2>&1).Trim()
    if ($LASTEXITCODE -or $pythonVersionText -notmatch 'Python\s+(\d+\.\d+\.\d+)') {
        throw "无法识别 Python 版本：$pythonVersionText。请重新安装 Python 3.11 或更高版本。"
    }
    $pythonVersion = $Matches[1]
    if ([version]$pythonVersion -lt [version]'3.11') {
        throw "Python 版本为 $pythonVersion，需要 3.11 或更高版本。"
    }
    $nodeVersionText = (& $node.Source --version).Trim().TrimStart('v')
    if ($LASTEXITCODE -or ([version]$nodeVersionText -lt [version]'20.0')) {
        throw "Node.js 版本为 $nodeVersionText，需要 20 或更高版本。"
    }
    $npmVersion = (& $npm.Source --version).Trim()
    if ($LASTEXITCODE) { throw 'npm 无法运行，请修复 Node.js 安装。' }

    & $python.Source -c 'import fastapi, uvicorn, pydantic, langgraph, cryptography' *> $null
    if ($LASTEXITCODE) {
        throw 'Python 依赖未安装。请依次运行：python -m pip install -r requirements.lock；python -m pip install --no-deps -e .'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $root 'apps\web\node_modules\vite'))) {
        throw '前端依赖未安装。请运行：cd apps\web；npm ci'
    }
    Write-Host "  Python $pythonVersion · Node.js $nodeVersionText · npm $npmVersion" -ForegroundColor Green
    return @{ Python = $python.Source; Node = $node.Source; Npm = $npm.Source }
}

function Assert-PortsFree([int[]]$Ports) {
    foreach ($port in $Ports) {
        if (Test-PortOpen $port) {
            throw "端口 $port 已被占用。请关闭占用该端口的程序，或停止已有开发服务后重试。"
        }
    }
}

function Wait-Http([string]$Url, [int]$Seconds = 30) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { return }
        } catch {
            Start-Sleep -Milliseconds 300
        }
    } while ((Get-Date) -lt $deadline)
    throw "服务在 $Seconds 秒内未就绪：$Url。请查看 data\logs 中的错误日志。"
}

function Open-YuwangBrowser([string]$Url) {
    # 地址由启动流程实际读取的端口生成，避免双击入口与 .env 的端口配置脱节。
    try {
        Start-Process -FilePath $Url
        Write-Host "已请求系统默认浏览器打开：$Url"
    } catch {
        Write-Warning "服务已启动，但无法自动打开浏览器。请手动访问：$Url"
    }
}

function Assert-NoTrackedDevelopmentProcesses {
    if (-not (Test-Path -LiteralPath $processFile)) { return }
    $tracked = Read-YuwangProcessRecord $processFile $root
    $alive = @()
    $unverified = @()
    foreach ($item in @($tracked.processes)) {
        $process = Get-YuwangVerifiedProcess $item $root
        if ($process) {
            $alive += $process.Id
        } elseif (Get-Process -Id ([int]$item.pid) -ErrorAction SilentlyContinue) {
            $unverified += [int]$item.pid
        }
    }
    if ($unverified.Count -gt 0) {
        throw "进程记录中的 PID $($unverified -join '、') 已被其他进程复用。请人工核对后删除 data\dev-processes.json。"
    }
    if ($alive.Count -gt 0) {
        throw "检测到本项目已有本地开发进程（PID：$($alive -join '、')）。请运行 .\yuwang.ps1 stop -Development，避免重复启动。"
    }
    Remove-Item -LiteralPath $processFile -Force
}

function Start-Development {
    param([switch]$OpenBrowser)
    if ($Build) { throw '-Build 只用于 Docker 模式，不能与 -Development 同时使用。' }
    $commands = Assert-DevelopmentDependencies
    $values = Read-DotEnv
    Import-YuwangEnvironment $values
    # 本地模式使用独立数据目录，避免与正在运行的 Docker SQLite 文件争用。
    [Environment]::SetEnvironmentVariable(
        'YUWANG_DATABASE_PATH', 'data/development/yuwang.db', 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'YUWANG_ARTIFACT_ROOT', 'data/development/artifacts', 'Process'
    )
    Assert-NoTrackedDevelopmentProcesses
    Assert-PortsFree @(8000, 5173)

    $cors = [Environment]::GetEnvironmentVariable('YUWANG_CORS_ORIGINS', 'Process')
    if ($cors -notmatch 'localhost:5173') {
        [Environment]::SetEnvironmentVariable(
            'YUWANG_CORS_ORIGINS',
            "http://localhost:5173,http://127.0.0.1:5173,$cors".TrimEnd(','),
            'Process'
        )
    }
    if ($CheckOnly) {
        Write-Host '本地开发环境检查通过；未启动任何进程。' -ForegroundColor Green
        return
    }

    [IO.Directory]::CreateDirectory($logDirectory) | Out-Null
    $apiOut = Join-Path $logDirectory 'api.stdout.log'
    $apiError = Join-Path $logDirectory 'api.stderr.log'
    $webOut = Join-Path $logDirectory 'web.stdout.log'
    $webError = Join-Path $logDirectory 'web.stderr.log'
    $viteEntry = Join-Path $root 'apps\web\node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $viteEntry)) {
        throw '未找到 Vite 启动入口。请在 apps\web 目录运行 npm ci。'
    }
    $apiProcess = $null
    $webProcess = $null
    try {
        Write-Host '正在启动本地 API 和 Web…'
        $apiOptions = @{
            FilePath = $commands.Python
            ArgumentList = @('-m','uvicorn','apps.api.main:app','--port','8000')
            WorkingDirectory = $root
            WindowStyle = 'Hidden'
            PassThru = $true
            RedirectStandardOutput = $apiOut
            RedirectStandardError = $apiError
        }
        $apiProcess = Start-Process @apiOptions
        $webOptions = @{
            # npm.cmd 会先退出再留下 node 子进程，导致 PID 记录失效；直接启动已安装的
            # Vite 入口，才能让 stop 和启动验收始终精确管理真正监听 5173 的进程。
            FilePath = $commands.Node
            ArgumentList = @($viteEntry,'--host','0.0.0.0','--port','5173')
            WorkingDirectory = (Join-Path $root 'apps\web')
            WindowStyle = 'Hidden'
            PassThru = $true
            RedirectStandardOutput = $webOut
            RedirectStandardError = $webError
        }
        $webProcess = Start-Process @webOptions
        New-YuwangProcessRecord $root @(
            @{ role = 'api'; pid = $apiProcess.Id },
            @{ role = 'web'; pid = $webProcess.Id }
        ) |
            ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $processFile -Encoding UTF8

        Wait-Http 'http://127.0.0.1:8000/api/v1/health'
        Wait-Http 'http://127.0.0.1:5173'
        # Windows 上 localhost 可能优先解析为未监听的 IPv6 ::1；统一展示可验证的 IPv4 回环地址。
        $webUrl = 'http://127.0.0.1:5173'
        if ($OpenBrowser) { Open-YuwangBrowser $webUrl }
        Write-Host ''
        Write-Host '本地开发服务启动成功：' -ForegroundColor Green
        Write-Host "  Web：        $webUrl"
        Write-Host '  API：        http://127.0.0.1:8000'
        Write-Host '  健康检查：   http://127.0.0.1:8000/api/v1/health'
        Write-Host "  日志位置：   $logDirectory"
        Write-Host '  停止方法：   在当前窗口按 Ctrl+C'
        Write-Host ''
        $stopAt = if ($RunSeconds -gt 0) { (Get-Date).AddSeconds($RunSeconds) } else { $null }
        while (-not $apiProcess.HasExited -and -not $webProcess.HasExited) {
            if ($stopAt -and (Get-Date) -ge $stopAt) {
                Write-Host '自动启动验收时间已到，准备清理进程。'
                return
            }
            Start-Sleep -Seconds 1
        }
        if (-not (Test-Path -LiteralPath $processFile)) {
            Write-Host '本地开发服务已由统一入口安全停止。' -ForegroundColor Green
            return
        }
        throw 'API 或 Web 意外退出，请查看 data\logs 中的 stderr 日志。'
    } finally {
        Write-Host '正在清理本次启动的本地进程…'
        if ($webProcess) { Stop-YuwangProcessTree $webProcess.Id }
        if ($apiProcess) { Stop-YuwangProcessTree $apiProcess.Id }
        Remove-Item -LiteralPath $processFile -Force -ErrorAction SilentlyContinue
    }
}

function Start-Docker {
    param([switch]$OpenBrowser)
    if ($RunSeconds -gt 0) { throw '-RunSeconds 只用于本地开发启动验收。' }
    Write-Step '检查可选的本地开发工具'
    Show-OptionalCommand 'python' '本地开发模式'
    Show-OptionalCommand 'node' '本地开发模式'
    Show-OptionalCommand 'npm' '本地开发模式'

    & (Join-Path $PSScriptRoot 'preflight.ps1')
    $values = Read-DotEnv
    $webPort = 8080
    if ($values.ContainsKey('YUWANG_WEB_PORT')) { $webPort = [int]$values['YUWANG_WEB_PORT'] }
    Push-Location $root
    try {
        $running = @(docker compose ps --status running --services)
        if ((Test-PortOpen $webPort) -and $running -notcontains 'web') {
            throw "端口 $webPort 已被其他程序占用。请关闭占用程序，或在 .env 中修改 YUWANG_WEB_PORT。"
        }
        if ($CheckOnly) {
            Write-Host 'Docker 启动检查通过；未创建或重启容器。' -ForegroundColor Green
            return
        }
        if ($running -contains 'web' -and -not $Build) {
            Write-Host '检测到项目容器已运行，将复用现有服务，不会重复启动。'
        } else {
            Write-Host '正在启动 Docker 服务，首次构建可能需要几分钟…'
        }
        $arguments = @('compose', 'up', '-d', '--wait')
        if ($Build) { $arguments += '--build' }
        if ($Quiet) {
            [IO.Directory]::CreateDirectory($logDirectory) | Out-Null
            $previousPreference = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                & docker $arguments *> (Join-Path $logDirectory 'compose-start.log')
                $composeExitCode = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $previousPreference
            }
        } else {
            & docker $arguments
            $composeExitCode = $LASTEXITCODE
        }
        if ($composeExitCode) {
            throw 'Docker Compose 启动失败。请运行 docker compose logs 查看具体原因。'
        }
    } finally {
        Pop-Location
    }
    Wait-Http "http://127.0.0.1:$webPort/api/v1/health" 30
    # Docker 端口探针已使用 127.0.0.1；浏览器和状态输出也必须使用同一个可达地址。
    $webUrl = "http://127.0.0.1:$webPort"
    if ($OpenBrowser) { Open-YuwangBrowser $webUrl }

    Write-Host ''
    Write-Host 'Docker 服务启动成功：' -ForegroundColor Green
    Write-Host "  Web：        $webUrl"
    Write-Host "  API：        http://127.0.0.1:$webPort/api/v1"
    Write-Host "  健康检查：   http://127.0.0.1:$webPort/api/v1/health"
    Write-Host '  日志位置：   Docker 容器日志（docker compose logs -f）'
    Write-Host '  状态检查：   docker compose ps'
    Write-Host '  停止方法：   docker compose down'
    Write-Host '  更新重建：   .\scripts\start.ps1 -Build'
}

# 首次运行只生成缺失的 .env，不输出密钥；具体模式再执行对应检查。
& (Join-Path $PSScriptRoot 'first-setup.ps1') -SkipPreflight
if ($Development) { Start-Development -OpenBrowser:$OpenBrowser } else { Start-Docker -OpenBrowser:$OpenBrowser }
