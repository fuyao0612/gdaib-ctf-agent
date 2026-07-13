# 快速质量入口：与 CI 的后端、前端基础门禁保持一致。
$ErrorActionPreference = 'Stop'

function Invoke-Checked([string]$Name, [scriptblock]$Action) {
    Write-Host "[快速检查] $Name" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE) { throw "$Name 失败（退出码 $LASTEXITCODE）。" }
}

Invoke-Checked 'Ruff' { ruff check . }
Invoke-Checked 'mypy' { mypy }
Invoke-Checked 'pytest' { pytest }
Push-Location apps/web
try {
    Invoke-Checked '前端 ESLint' { npm run lint }
    Invoke-Checked '前端 TypeScript' { npm run typecheck }
    Invoke-Checked '前端 Vitest' { npm test }
    Invoke-Checked '前端生产构建' { npm run build }
} finally {
    Pop-Location
}

Write-Host '快速检查全部通过。' -ForegroundColor Green
