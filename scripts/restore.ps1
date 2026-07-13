# 在停服窗口恢复备份；先校验目标路径，避免覆盖项目外文件。
[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param([Parameter(Mandatory)][string]$Backup, [string]$DataPath = (Join-Path $PSScriptRoot '..\data'), [switch]$Force)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$archive = (Resolve-Path -LiteralPath $Backup).Path
$data = [IO.Path]::GetFullPath($DataPath)
if (-not ($data + [IO.Path]::DirectorySeparatorChar).StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Restore path must be inside the project directory.' }
if (-not $Force -and -not $PSCmdlet.ShouldProcess($data, 'replace data with backup')) { return }
Push-Location $root
try { docker compose down; if (Test-Path $data) { Remove-Item -LiteralPath $data -Recurse -Force }; New-Item -ItemType Directory -Path $data | Out-Null; Expand-Archive -LiteralPath $archive -DestinationPath $data }
finally { Pop-Location }
Write-Host 'Restore completed. Use the YUWANG_MASTER_KEY associated with this backup.'
