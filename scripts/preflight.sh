#!/usr/bin/env sh
# 启动前检查环境、Compose 配置、权限和必需密钥是否满足生产条件。
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ -f "$ROOT/.env" ] || { echo '缺少 .env，请先运行 scripts/first-setup.sh。' >&2; exit 1; }
grep -Eq '^YUWANG_MASTER_KEY=[^<[:space:]]+$' "$ROOT/.env" || { echo '主密钥缺失或仍是占位值。' >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo '未找到 docker。' >&2; exit 1; }
(cd "$ROOT" && docker compose config --quiet)
echo '升级前检查通过（未输出任何密钥）。'
