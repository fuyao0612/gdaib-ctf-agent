#!/usr/bin/env sh
# 停止 API 后制作一致性数据备份；退出或中断时务必恢复服务。
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DATA=${2:-"$ROOT/data"}; DEST=${1:-"$PWD/yuwang-backup-$(date +%Y%m%d-%H%M%S).tgz"}
DATA=$(CDPATH= cd -- "$DATA" && pwd)
case "$DATA/" in "$ROOT"/*) ;; *) echo '数据目录必须位于项目目录内。' >&2; exit 1;; esac
cd "$ROOT"; docker compose stop api
trap 'docker compose start api >/dev/null' EXIT INT TERM
tar -czf "$DEST" -C "$DATA" .
docker compose start api >/dev/null; trap - EXIT INT TERM
echo "一致性备份已写入 $DEST（请分离保管 .env）。"
