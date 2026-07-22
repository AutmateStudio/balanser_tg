#!/usr/bin/env bash
# Быстрый фикс после неудачного деплоя: права data/, дубликаты .env, health.
#   bash scripts/fix_env_and_permissions_vps104.sh
set -uo pipefail

SD="${HOME}/Lidogen_telegram_balancer/standalone_discovery"
ENV="${SD}/.env"

echo "=== chown data/sessions ==="
if command -v sudo >/dev/null 2>&1; then
  sudo chown -R "$(whoami):$(whoami)" "${SD}/data" "${SD}/sessions"
fi
echo ">>> ОЖИДАНИЕ: без Permission denied на ls data/"

echo "=== dedupe .env ==="
dedupe_key() {
  local key="$1"
  local val="$2"
  local tmp
  tmp="$(mktemp)"
  grep -vE "^${key}=" "$ENV" > "$tmp" || true
  echo "${key}=${val}" >> "$tmp"
  mv "$tmp" "$ENV"
}

[ -f "$ENV" ] || { echo "нет $ENV"; exit 1; }

dedupe_key "DISCOVERY_APP_PORT" "8100"
dedupe_key "USE_PG_QUEUE" "true"
dedupe_key "DISCOVERY_INPROCESS_WORKER" "true"
dedupe_key "WORKER_TASK_ADAPTER" "clump"
dedupe_key "INPROCESS_WORKER_COUNT" "4"
dedupe_key "JOIN_PENDING_RETRY_SECONDS" "1800"

echo "Ключи .env:"
grep -E '^(DISCOVERY_APP_PORT|USE_PG_QUEUE|DISCOVERY_INPROCESS_WORKER|INPROCESS_WORKER_COUNT)=' "$ENV"
echo ">>> ОЖИДАНИЕ: по одной строке на ключ, PORT=8100"

PORT="$(grep ^DISCOVERY_APP_PORT= "$ENV" | tail -1 | cut -d= -f2- | tr -d '\r')"
echo "=== health http://127.0.0.1:${PORT}/health ==="
curl -sS -w "\nHTTP:%{http_code}\n" "http://127.0.0.1:${PORT}/health"
echo ">>> ОЖИДАНИЕ: HTTP:200"
