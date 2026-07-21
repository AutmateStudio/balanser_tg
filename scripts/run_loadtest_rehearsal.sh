#!/usr/bin/env bash
# Rehearsal 2x5 на prod (короткий прогон перед 20x100).
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
psql "$QUEUE_DATABASE_URL" -c \
  "SELECT pickable_accounts_count, busy_accounts_count, orphan_account_locks FROM v_accounts_overview;"

echo "== rehearsal 2x5 =="
exec python3 -m loadtest.prod_e2e \
  --scale 2x5 \
  --enqueue-check-after "${LOADTEST_ENQUEUE_CHECK_AFTER:-60}" \
  --change-duration "${LOADTEST_CHANGE_DURATION:-120}" \
  --final-collect "${LOADTEST_FINAL_COLLECT:-60}" \
  "$@"
