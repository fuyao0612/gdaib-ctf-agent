# 本地一键执行与 CI 相同的后端和前端质量门禁。
$ErrorActionPreference = "Stop"
ruff check .
mypy
pytest
Push-Location apps/web
try {
  npm run lint
  npm run typecheck
  npm test
  npm run build
} finally {
  Pop-Location
}
