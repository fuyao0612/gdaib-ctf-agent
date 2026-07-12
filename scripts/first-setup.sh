#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE="$ROOT/.env"
if [ -e "$ENV_FILE" ]; then
  echo '.env 已存在，未作任何修改。'
else
  command -v python3 >/dev/null 2>&1 || { echo '需要 python3 生成安全随机密钥。' >&2; exit 1; }
  ADMIN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  MASTER=$(python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')
  umask 077
  printf '%s\n' "YUWANG_ADMIN_TOKEN=$ADMIN" "YUWANG_MASTER_KEY=$MASTER" \
    'YUWANG_CORS_ORIGINS=http://localhost:8080' 'YUWANG_COOKIE_SECURE=false' \
    'YUWANG_WEB_PORT=8080' 'YUWANG_DATA_PATH=./data' 'YUWANG_API_CPUS=1.0' \
    'YUWANG_API_MEMORY=768M' 'YUWANG_WEB_CPUS=0.5' 'YUWANG_WEB_MEMORY=192M' > "$ENV_FILE"
  echo '已生成 .env（密钥未输出）。请离线保存管理员令牌与主密钥。'
fi
"$ROOT/scripts/preflight.sh"
if [ "${1:-}" = '--start' ]; then cd "$ROOT" && docker compose up -d --build; fi
