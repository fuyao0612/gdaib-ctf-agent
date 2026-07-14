# 本地开发进程记录与安全停止逻辑。PID 必须同时匹配启动时间，避免误杀复用 PID。

function New-YuwangProcessRecord([string]$ProjectRoot, [object[]]$Processes) {
    return @{
        schema_version = 1
        project_root = [IO.Path]::GetFullPath($ProjectRoot)
        created_at = [datetime]::UtcNow.ToString('o')
        processes = @($Processes | ForEach-Object {
            $process = Get-Process -Id $_.pid -ErrorAction Stop
            @{
                role = $_.role
                pid = [int]$process.Id
                started_at = $process.StartTime.ToUniversalTime().ToString('o')
            }
        })
    }
}

function Get-YuwangVerifiedProcess([object]$Record, [string]$ProjectRoot) {
    if (-not $Record -or -not $Record.pid -or -not $Record.started_at) { return $null }
    try {
        $process = Get-Process -Id ([int]$Record.pid) -ErrorAction Stop
        $expected = [datetime]::Parse([string]$Record.started_at).ToUniversalTime()
        $actual = $process.StartTime.ToUniversalTime()
        if ([Math]::Abs(($actual - $expected).TotalSeconds) -gt 1) { return $null }
        return $process
    } catch {
        return $null
    }
}

function Read-YuwangProcessRecord([string]$ProcessFile, [string]$ProjectRoot) {
    if (-not (Test-Path -LiteralPath $ProcessFile)) { return $null }
    try {
        $record = Get-Content -Raw -LiteralPath $ProcessFile | ConvertFrom-Json
    } catch {
        throw '本地进程记录已损坏。为防止误杀，脚本不会停止任何进程；请人工核对 data\dev-processes.json。'
    }
    if ($record.schema_version -ne 1 -or -not $record.project_root -or -not $record.processes) {
        throw '本地进程记录版本无法安全验证。请人工确认旧进程后删除 data\dev-processes.json。'
    }
    $expectedRoot = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $recordedRoot = [IO.Path]::GetFullPath([string]$record.project_root).TrimEnd('\')
    if (-not [string]::Equals($expectedRoot, $recordedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw '进程记录不属于当前项目目录。为防止误杀，脚本不会停止任何进程。'
    }
    return $record
}

function Stop-YuwangProcessTree([int]$TargetProcessId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $TargetProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) { Stop-YuwangProcessTree ([int]$child.ProcessId) }
    Stop-Process -Id $TargetProcessId -Force -ErrorAction SilentlyContinue
}
