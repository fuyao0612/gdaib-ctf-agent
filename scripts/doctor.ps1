# 只读诊断：不安装依赖、不改配置、不创建测试文件，也不输出任何密钥值。

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'operations-common.ps1')
$results = @()

function Add-Diagnostic([string]$Level, [string]$Name, [string]$Detail, [string]$Solution) {
    $script:results += [pscustomobject]@{
        Level = $Level
        Name = $Name
        Detail = $Detail
        Solution = $Solution
    }
}

function Test-DirectoryWritableByAcl([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    if ((Get-Item -LiteralPath $Path).Attributes -band [IO.FileAttributes]::ReadOnly) {
        return $false
    }
    if ($env:OS -ne 'Windows_NT') { return $true }
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $sids = @($identity.User.Value) + @($identity.Groups | ForEach-Object { $_.Value })
        $rules = (Get-Acl -LiteralPath $Path).GetAccessRules(
            $true, $true, [Security.Principal.SecurityIdentifier]
        )
        $writeRights = [Security.AccessControl.FileSystemRights]::WriteData -bor
            [Security.AccessControl.FileSystemRights]::CreateFiles -bor
            [Security.AccessControl.FileSystemRights]::Modify -bor
            [Security.AccessControl.FileSystemRights]::FullControl
        $allowed = $false
        foreach ($rule in $rules) {
            if ($sids -notcontains $rule.IdentityReference.Value) { continue }
            if (-not ($rule.FileSystemRights -band $writeRights)) { continue }
            if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Deny) {
                return $false
            }
            $allowed = $true
        }
        return $allowed
    } catch {
        return $false
    }
}

function Test-FernetKeyShape([string]$Value) {
    try {
        $normalized = $Value.Replace('-', '+').Replace('_', '/')
        return ([Convert]::FromBase64String($normalized).Length -eq 32)
    } catch {
        return $false
    }
}

function Invoke-ReadOnlyPython([string]$Executable, [string[]]$Arguments) {
    # pytest-cov 会把覆盖率变量传给子进程；诊断必须临时移除它们，且禁止生成 pyc。
    $names = @(
        'COV_CORE_SOURCE','COV_CORE_CONFIG','COV_CORE_DATAFILE',
        'COVERAGE_PROCESS_START','PYTHONDONTWRITEBYTECODE'
    )
    $saved = @{}
    foreach ($name in $names) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', '1', 'Process')
    try {
        $output = & $Executable @Arguments 2>&1
        $exitCode = $LASTEXITCODE
        return @{ Output = @($output) -join [Environment]::NewLine; ExitCode = $exitCode }
    } finally {
        foreach ($name in $names) {
            [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
        }
    }
}

function Test-NativeCommand([scriptblock]$Command) {
    # PowerShell 5.1 会把原生程序 stderr 包装成错误记录；探针只看退出码，不能让诊断中断。
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Command *> $null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    $versionResult = Invoke-ReadOnlyPython $python.Source @('--version')
    $text = $versionResult.Output.Trim()
    if ($text -match 'Python\s+(\d+\.\d+\.\d+)' -and [version]$Matches[1] -ge [version]'3.11') {
        Add-Diagnostic '正常' 'Python' $text ''
    } else {
        Add-Diagnostic '失败' 'Python' $text '安装 Python 3.11 或更高版本，并重新打开 PowerShell。'
    }
} else {
    Add-Diagnostic '失败' 'Python' '未安装' '安装 Python 3.11 或更高版本，并重新打开 PowerShell。'
}

$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    $versionText = (& $node.Source --version).Trim().TrimStart('v')
    if ([version]$versionText -ge [version]'20.0') {
        Add-Diagnostic '正常' 'Node.js' "v$versionText" ''
    } else {
        Add-Diagnostic '失败' 'Node.js' "v$versionText" '安装 Node.js 20 或更高版本。'
    }
} else {
    Add-Diagnostic '失败' 'Node.js' '未安装' '安装 Node.js 20 或更高版本。'
}

$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if ($npm) {
    Add-Diagnostic '正常' 'npm' ((& $npm.Source --version).Trim()) ''
} else {
    Add-Diagnostic '失败' 'npm' '未安装' '重新安装 Node.js，npm 会随其安装。'
}

$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) {
    if (-not (Test-NativeCommand { docker info })) {
        Add-Diagnostic '警告' 'Docker' '已安装但服务未运行' '需要容器模式时启动 Docker Desktop；本地开发模式不受影响。'
    } else {
        if (-not (Test-NativeCommand { docker compose version })) {
            Add-Diagnostic '失败' 'Docker' '缺少 Compose v2' '更新 Docker Desktop。'
        } else {
            Add-Diagnostic '正常' 'Docker' 'Docker 与 Compose v2 可用' ''
        }
    }
} else {
    Add-Diagnostic '警告' 'Docker' '未安装' '推荐安装 Docker Desktop；也可使用本地开发模式。'
}

$envFile = Join-Path $ProjectRoot '.env'
$values = Get-YuwangEnvValues $ProjectRoot
if (-not (Test-Path -LiteralPath $envFile)) {
    Add-Diagnostic '失败' '.env' '不存在' '运行 .\yuwang.ps1 setup 生成本机配置。'
} else {
    $adminOk = $values.ContainsKey('YUWANG_ADMIN_TOKEN') -and
        $values['YUWANG_ADMIN_TOKEN'].Length -ge 32 -and
        -not $values['YUWANG_ADMIN_TOKEN'].StartsWith('<')
    $masterOk = $values.ContainsKey('YUWANG_MASTER_KEY') -and
        (Test-FernetKeyShape $values['YUWANG_MASTER_KEY'])
    if ($adminOk -and $masterOk) {
        Add-Diagnostic '正常' '.env' '关键配置有效，敏感值未输出' ''
    } else {
        Add-Diagnostic '失败' '.env' '管理员令牌或主密钥无效' '安全备份后修正配置；不要把密钥发送到聊天或日志。'
    }
}

$dataPath = if ($values.ContainsKey('YUWANG_DATA_PATH')) { $values['YUWANG_DATA_PATH'] } else { './data' }
if (-not [IO.Path]::IsPathRooted($dataPath)) { $dataPath = Join-Path $ProjectRoot $dataPath }
if (Test-DirectoryWritableByAcl $dataPath) {
    Add-Diagnostic '正常' '数据目录' '目录存在，ACL 允许当前用户写入（未创建测试文件）' ''
} else {
    Add-Diagnostic '失败' '数据目录' '目录不存在、只读或 ACL 未授予写入权限' '检查 data 目录权限；诊断保持只读，不会自动修改。'
}

if ($python) {
    $importResult = Invoke-ReadOnlyPython $python.Source @(
        '-c', 'import fastapi, uvicorn, pydantic, langgraph, cryptography'
    )
    if ($importResult.ExitCode) {
        Add-Diagnostic '失败' 'Python 依赖' '不完整' '运行 python -m pip install -r requirements.lock，然后运行 python -m pip install --no-deps -e .'
    } else {
        Add-Diagnostic '正常' 'Python 依赖' '核心模块可导入' ''
    }
}

$vite = Join-Path $ProjectRoot 'apps\web\node_modules\vite\package.json'
if ($npm -and (Test-Path -LiteralPath $vite)) {
    Push-Location (Join-Path $ProjectRoot 'apps\web')
    try {
        npm.cmd list --depth=0 --silent *> $null
        if ($LASTEXITCODE) {
            Add-Diagnostic '失败' '前端依赖' '依赖树不完整' '在 apps\web 目录运行 npm ci。'
        } else {
            Add-Diagnostic '正常' '前端依赖' 'package-lock 对应依赖已安装' ''
        }
    } finally {
        Pop-Location
    }
} else {
    Add-Diagnostic '失败' '前端依赖' '未安装' '在 apps\web 目录运行 npm ci。'
}

$webPort = Get-YuwangWebPort $values
$dockerBase = "http://127.0.0.1:$webPort"
$devBase = 'http://127.0.0.1:8000'
$dockerHealth = Invoke-YuwangEndpoint "$dockerBase/api/v1/health"
$devHealth = Invoke-YuwangEndpoint "$devBase/api/v1/health"
$apiBase = if ($dockerHealth.StatusCode -eq 200) { $dockerBase } elseif ($devHealth.StatusCode -eq 200) { $devBase } else { $null }

foreach ($port in @($webPort, 8000, 5173) | Select-Object -Unique) {
    $open = Test-YuwangPortOpen $port
    $expected = ($port -eq $webPort -and $dockerHealth.StatusCode -eq 200) -or
        ($port -eq 8000 -and $devHealth.StatusCode -eq 200) -or
        ($port -eq 5173 -and $devHealth.StatusCode -eq 200)
    if (-not $open) {
        Add-Diagnostic '正常' "端口 $port" '空闲' ''
    } elseif ($expected) {
        Add-Diagnostic '正常' "端口 $port" '由当前御网智元服务使用' ''
    } else {
        Add-Diagnostic '警告' "端口 $port" '被其他服务占用' '关闭占用程序；Docker Web 端口也可在 .env 中修改。'
    }
}

if ($apiBase) {
    Add-Diagnostic '正常' 'API/Web' "服务可访问：$apiBase" ''
    $setup = Invoke-YuwangEndpoint "$apiBase/api/v1/setup/status"
    if ($setup.StatusCode -eq 200 -and $setup.Body) {
        $checks = $setup.Body.checks
        Add-Diagnostic $(if ($checks.database) { '正常' } else { '失败' }) '数据库' $(if ($checks.database) { '连接正常' } else { '连接失败' }) $(if ($checks.database) { '' } else { '查看 API 日志并检查数据目录权限。' })
        Add-Diagnostic $(if ($checks.provider) { '正常' } else { '警告' }) 'Provider' $(if ($checks.provider) { '至少一个已连接 Provider 可用' } else { '尚无连接成功的 Provider' }) $(if ($checks.provider) { '' } else { '打开设置中心，填写正规厂商 API 并执行连接测试。' })
        $agentReady = if ($checks.PSObject.Properties.Name -contains 'agent') { [bool]$checks.agent } else { [bool]$checks.provider }
        Add-Diagnostic $(if ($agentReady) { '正常' } else { '警告' }) '默认 Agent' $(if ($agentReady) { '可以开始对话' } else { '尚未绑定可用 Provider' }) $(if ($agentReady) { '' } else { '在设置中心确认推荐 Agent，并设为默认。' })
    } else {
        Add-Diagnostic '失败' '配置状态' '公开状态接口不可用' '查看 API 日志。'
    }
} else {
    Add-Diagnostic '警告' 'API/Web' '服务未运行' '运行 .\yuwang.ps1 start。'
    Add-Diagnostic '警告' 'Provider/Agent' '服务未运行，无法诊断就绪状态' '启动服务后重新运行 doctor。'
}

Write-Host '御网智元只读诊断' -ForegroundColor Cyan
foreach ($item in $results) {
    $color = if ($item.Level -eq '正常') { 'Green' } elseif ($item.Level -eq '警告') { 'Yellow' } else { 'Red' }
    Write-Host ("[{0}] {1}：{2}" -f $item.Level, $item.Name, $item.Detail) -ForegroundColor $color
    if ($item.Solution) { Write-Host "       解决办法：$($item.Solution)" }
}
$normal = @($results | Where-Object Level -eq '正常').Count
$warning = @($results | Where-Object Level -eq '警告').Count
$failed = @($results | Where-Object Level -eq '失败').Count
Write-Host "诊断汇总：正常 $normal，警告 $warning，失败 $failed。"
