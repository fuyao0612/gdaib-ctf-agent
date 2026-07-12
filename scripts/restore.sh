#!/usr/bin/env sh
set -eu
[ "$#" -ge 1 ] || { echo '用法: restore.sh BACKUP [DATA_PATH] --force' >&2; exit 2; }
[ "${3:-}" = '--force' ] || { echo '恢复会替换数据，请显式传入 --force。' >&2; exit 2; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd); BACKUP=$(CDPATH= cd -- "$(dirname -- "$1")" && pwd)/$(basename -- "$1"); DATA=${2:-"$ROOT/data"}
DATA_PARENT=$(CDPATH= cd -- "$(dirname -- "$DATA")" && pwd); DATA="$DATA_PARENT/$(basename -- "$DATA")"
case "$DATA/" in "$ROOT"/*) ;; *) echo '恢复目录必须位于项目目录内。' >&2; exit 1;; esac
cd "$ROOT"; docker compose down; rm -rf -- "$DATA"; mkdir -p -- "$DATA"; tar -xzf "$BACKUP" -C "$DATA"
echo '恢复完成。启动前确认使用备份对应的 YUWANG_MASTER_KEY。'
