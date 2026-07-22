# 快速质量入口：与 CI 的后端、前端基础门禁保持一致。
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
# Windows 上上一次测试可能由不同权限的进程留下目录；固定名称会让 pytest 在清理阶段失败。
# 每次检查使用当前 PowerShell 的隔离目录，既不会触碰系统临时目录，也不会和并发验收冲突。
$pytestBaseTemp = Join-Path $root ".tmp-pytest-check-$PID"

function Invoke-Checked([string]$Name, [scriptblock]$Action) {
    Write-Host "[快速检查] $Name" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE) { throw "$Name 失败（退出码 $LASTEXITCODE）。" }
}

Invoke-Checked 'Ruff' { ruff check . }
Invoke-Checked 'mypy' { mypy }
# 某些受管 Windows 环境不允许枚举系统 pytest 临时目录。把测试临时文件限制在
# 工作区的已忽略目录内，既不触碰用户的系统临时文件，也让统一质量入口可重复运行。
Invoke-Checked 'pytest' { pytest --basetemp $pytestBaseTemp -o "cache_dir=$pytestBaseTemp\cache" }
Push-Location apps/web
try {
    # 在受执行策略限制的 Windows PowerShell 中，npm.ps1 会被拦截；统一调用
    # npm.cmd，和启动脚本保持一致，同时不影响 macOS/Linux 的 CI（它们不执行此脚本）。
    Invoke-Checked '前端 ESLint' { npm.cmd run lint }
    Invoke-Checked '前端 TypeScript' { npm.cmd run typecheck }
    Invoke-Checked '前端 Vitest' { npm.cmd test }
    Invoke-Checked '前端生产构建' { npm.cmd run build }
} finally {
    Pop-Location
}

Write-Host '快速检查全部通过。' -ForegroundColor Green
