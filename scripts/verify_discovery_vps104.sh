#!/usr/bin/env bash
# Проверка discovery-api после деплоя (vps-104).
#   bash scripts/verify_discovery_vps104.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SD="${ROOT}/standalone_discovery"
ENV="${SD}/.env"
FAIL=0

warn() { echo "WARN: $*"; }
fail() { echo "FAIL: $*"; FAIL=1; }

[ -f "$ENV" ] || { echo "нет ${ENV}"; exit 1; }

# Порт: последнее значение DISCOVERY_APP_PORT, иначе 8100; убрать CR/quotes.
PORT="$(grep ^DISCOVERY_APP_PORT= "$ENV" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r\"'"'"' ' || true)"
PORT="${PORT:-8100}"
API_KEY="$(grep ^API_KEY= "$ENV" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r' || true)"

echo "--- PORT=${PORT} ---"

echo "--- health ---"
HTTP="$(curl -sS -o /tmp/lidogen-health.json -w '%{http_code}' "http://127.0.0.1:${PORT}/health" 2>/dev/null || echo 000)"
cat /tmp/lidogen-health.json 2>/dev/null || true
echo ""
echo "HTTP: ${HTTP}"
if [ "$HTTP" != "200" ]; then
  fail "health HTTP=${HTTP} (ожидалось 200)"
fi

echo "--- docker ps ---"
docker compose -f "${SD}/docker-compose.yml" ps 2>/dev/null || warn "docker compose ps"

echo "--- worker pool / успехи ---"
docker compose -f "${SD}/docker-compose.yml" logs discovery-api --tail 100 2>/dev/null | \
  grep -E "D12|in-process worker pool|worker.*старт|add_channel OK|parser_add_channel completed|resolve OK|join_pending" || \
  warn "нет строк worker pool — смотрите: docker compose logs discovery-api --tail 50"

echo "--- parser_jobs.json ---"
python3 <<'PY' || warn "parser_jobs.json"
import json, os
p = os.path.expanduser("~/Lidogen_telegram_balancer/standalone_discovery/data/parser_jobs.json")
if not os.path.isfile(p):
    print("не найден:", p)
else:
    for job in json.load(open(p, encoding="utf-8")):
        pid = job.get("parser_id") or job.get("id")
        for s in job.get("sessions") or []:
            print(f"parser={pid} session={s.get('session_name')} channels={len(s.get('channels') or [])}")
PY

if command -v psql >/dev/null 2>&1; then
  PGURL="$(grep ^QUEUE_DATABASE_URL= "$ENV" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r' || true)"
  if [ -n "$PGURL" ]; then
    echo "--- PG: статусы parser_add_channel ---"
    psql "$PGURL" -c "
      SELECT status, COUNT(*) FROM task_queue
      WHERE task_type_code = 'parser_add_channel'
      GROUP BY status ORDER BY 2 DESC;
    " 2>/dev/null || warn "psql недоступен"
  fi
fi

if [ -n "$API_KEY" ]; then
  echo "--- metrics ---"
  curl -sS -H "X-API-Key: ${API_KEY}" \
    "http://127.0.0.1:${PORT}/discovery-api/parser/queue/metrics" 2>/dev/null | head -c 600 || warn "metrics недоступны"
  echo ""
else
  warn "API_KEY пуст"
fi

if [ "$FAIL" -ne 0 ]; then
  exit 1
fi
echo "OK: verify пройден"
