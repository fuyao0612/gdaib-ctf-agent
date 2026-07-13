# 停止 API 后制作一致性数据备份；.env 必须由运维人员分离保管。
[CmdletBinding()]
param([string]$Destination = (Join-Path (Get-Location) ("yuwang-backup-{0}.zip" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))), [string]$DataPath = (Join-Path $PSScriptRoot '..\data'))
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$data = (Resolve-Path -LiteralPath $DataPath).Path
if (-not ($data + [IO.Path]::DirectorySeparatorChar).StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Data path must be inside the project directory.' }
Push-Location $root
try { docker compose stop api; Compress-Archive -Path (Join-Path $data '*') -DestinationPath $Destination -Force }
finally { docker compose start api; Pop-Location }
Write-Host "Consistent backup written to $Destination. Store .env separately."
