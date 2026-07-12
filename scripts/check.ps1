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
