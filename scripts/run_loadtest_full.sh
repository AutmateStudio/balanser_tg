#!/usr/bin/env bash
# Полный 20x100 / ~2 часа.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -z "${QUEUE_DATABASE_URL:-}" ]]; then
  export QUEUE_DATABASE_URL="$(grep ^QUEUE_DATABASE_URL= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"
fi
if [[ -z "${API_KEY:-}" ]]; then
  export API_KEY="$(grep ^API_KEY= standalone_discovery/.env | cut -d= -f2- | tr -d '\r')"
fi

: "${LOADTEST_PARSER_ID:?set LOADTEST_PARSER_ID}"
: "${LOADTEST_OWNER_USER_ID:?set LOADTEST_OWNER_USER_ID}"

echo "== unlock zombies =="
psql "$QUEUE_DATABASE_URL" -v apply=1 -f scripts/ops_unlock_zombie_accounts.sql

echo "== full 20x100 (~2h) =="
exec python3 -m loadtest.prod_e2e \
  --scale 20x100 \
  --enqueue-check-after "${LOADTEST_ENQUEUE_CHECK_AFTER:-600}" \
  --change-duration "${LOADTEST_CHANGE_DURATION:-6000}" \
  --final-collect "${LOADTEST_FINAL_COLLECT:-600}" \
  "$@"
