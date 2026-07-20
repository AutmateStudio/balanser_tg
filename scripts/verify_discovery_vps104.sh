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
# Не делать `curl || echo 000`: при fail curl уже пишет http_code=000 → получится 000000.
HTTP="000"
rm -f /tmp/lidogen-health.json
for attempt in 1 2 3 4 5 6; do
  HTTP="$(curl -sS --connect-timeout 2 --max-time 5 \
    -o /tmp/lidogen-health.json -w '%{http_code}' \
    "http://127.0.0.1:${PORT}/health" 2>/dev/null || true)"
  HTTP="$(printf '%s' "$HTTP" | tr -d '\r\n')"
  if [ "$HTTP" = "200" ]; then
    break
  fi
  echo "попытка ${attempt}/6: HTTP=${HTTP:-000}, ждём 5с…"
  sleep 5
done
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

echo "--- parser_jobs.json: writer/reader consistency (в контейнере) ---"
# Регресс fix/accounts-sync-parser-store-path: discovery_api (writer) и
# accounts_sync (reader PG-синка) ДОЛЖНЫ резолвить один и тот же parser_jobs.json.
# Если пути расходятся — enroll пишет членство в один файл, а синк читает другой →
# in_clump=False → аккаунты становятся disabled.
CONS_OUT="$(docker compose -f "${SD}/docker-compose.yml" exec -T discovery-api python - <<'PY' 2>&1
import json, os, sys

rc = 0
try:
    from discovery_api.parser_store import _store_path as writer_fn
    writer = os.path.abspath(writer_fn())
except Exception as e:  # noqa: BLE001
    print("FAIL: не удалось получить writer path (discovery_api.parser_store):", e)
    sys.exit(2)

try:
    from app_balance.queue.accounts_sync import default_parser_store_path
    reader = os.path.abspath(default_parser_store_path())
except Exception as e:  # noqa: BLE001
    print("FAIL: не удалось получить reader path (accounts_sync):", e)
    sys.exit(2)

env_override = (os.getenv("PARSER_STORE_PATH") or "").strip()
print("writer (discovery_api)  :", writer)
print("reader (accounts_sync)  :", reader)
print("PARSER_STORE_PATH env   :", env_override or "(не задан)")
print("writer file exists      :", os.path.isfile(writer))

if writer != reader:
    print("FAIL: writer и reader parser_jobs.json РАСХОДЯТСЯ — синк прочитает не тот файл,")
    print(f"      аккаунты после enroll станут disabled. Задайте в .env PARSER_STORE_PATH={writer}")
    rc = 1
else:
    print("OK: writer == reader")

if not os.path.isfile(writer):
    print("WARN: файл членства clump отсутствует — новые enroll'ы дадут disabled")
else:
    try:
        data = json.load(open(writer, encoding="utf-8"))
        jobs = [j for j in data if isinstance(j, dict)]
        sess = 0
        for j in jobs:
            names = j.get("session_name_list")
            if not names and j.get("session_name"):
                names = [j["session_name"]]
            sess += len(names or [])
        print(f"OK: clump-записей={len(jobs)}, сессий в файле={sess}")
    except Exception as e:  # noqa: BLE001
        print("WARN: не удалось прочитать файл членства:", e)

sys.exit(rc)
PY
)"
CONS_RC=$?
echo "$CONS_OUT"
if [ "$CONS_RC" -ne 0 ]; then
  fail "parser_jobs.json writer/reader consistency (см. вывод выше)"
fi

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
