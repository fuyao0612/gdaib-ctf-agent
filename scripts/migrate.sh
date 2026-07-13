#!/usr/bin/env sh
# 显式运行幂等数据库迁移，并在失败时立即停止。
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$ROOT/scripts/preflight.sh"
cd "$ROOT"
docker compose run --rm api python -c "from apps.api.main import Settings,create_app; create_app(Settings()); print('配置与数据库迁移完成')"
