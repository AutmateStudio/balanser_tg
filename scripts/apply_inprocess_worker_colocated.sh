#!/usr/bin/env bash
# D12 Вариант A — in-process queue-worker на co-located хосте (напр. vps-104).
#
# Один процесс discovery-api: clump + listener + claim loop. Отдельный queue-worker
# и producer-balancer не запускать (конфликт .session SQLite).
#
# Использование (на сервере, из корня репо):
#   bash scripts/apply_inprocess_worker_colocated.sh
#   bash scripts/apply_inprocess_worker_colocated.sh --verify-only
set -eu
set -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISCOVERY_ENV="${ROOT}/standalone_discovery/.env"
VERIFY_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --verify-only) VERIFY_ONLY=true ;;
    -h|--help)
      echo "Usage: $0 [--verify-only]"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

require_env() {
  local key="$1"
  if ! grep -qE "^${key}=" "$DISCOVERY_ENV" 2>/dev/null; then
    echo "ERROR: в ${DISCOVERY_ENV} нет ${key}=" >&2
    exit 1
  fi
}

ensure_discovery_env() {
  if [[ ! -f "$DISCOVERY_ENV" ]]; then
    echo "ERROR: нет файла ${DISCOVERY_ENV}" >&2
    exit 1
  fi
  # Идемпотентно выставить флаги in-process worker
  for pair in \
    "DISCOVERY_INPROCESS_WORKER=true" \
    "WORKER_TASK_ADAPTER=clump" \
    "USE_PG_QUEUE=true" \
    "INPROCESS_WORKER_COUNT=4"
  do
    key="${pair%%=*}"
    if grep -qE "^${key}=" "$DISCOVERY_ENV"; then
      sed -i "s/^${key}=.*/${pair}/" "$DISCOVERY_ENV"
    else
      echo "$pair" >> "$DISCOVERY_ENV"
    fi
  done
  echo "OK: ${DISCOVERY_ENV} — DISCOVERY_INPROCESS_WORKER=true, WORKER_TASK_ADAPTER=clump, INPROCESS_WORKER_COUNT=4"
}

stop_competing_containers() {
  cd "$ROOT"
  docker compose stop queue-worker 2>/dev/null || true
  docker compose stop producer-balancer 2>/dev/null || true
  echo "OK: queue-worker и producer-balancer остановлены (если были запущены)"
}

recreate_discovery_api() {
  cd "${ROOT}/standalone_discovery"
  docker compose up -d --force-recreate discovery-api
  echo "OK: discovery-api пересоздан"
  sleep 3
  docker compose logs discovery-api --tail=40
}

verify_logs() {
  local logs
  logs="$(cd "${ROOT}/standalone_discovery" && docker compose logs discovery-api --tail=80 2>&1)"
  if echo "$logs" | grep -q "in-process worker pool запущен"; then
    echo "OK: in-process worker pool в логах"
  else
    echo "WARN: не найдена строка «in-process worker pool запущен»" >&2
  fi
  if echo "$logs" | grep -q "database is locked"; then
    echo "FAIL: в логах есть database is locked — проверьте, что queue-worker остановлен" >&2
    return 1
  fi
  echo "OK: database is locked не обнаружен в последних логах"
}

verify_metrics() {
  local api_key port base
  api_key="$(grep -E '^API_KEY=' "$DISCOVERY_ENV" | cut -d= -f2- | tr -d '\r' || true)"
  port="$(grep -E '^DISCOVERY_APP_PORT=' "$DISCOVERY_ENV" | cut -d= -f2- | tr -d '\r' || true)"
  port="${port:-8100}"
  base="http://127.0.0.1:${port}"
  if [[ -z "$api_key" ]]; then
    echo "WARN: API_KEY не задан — пропуск metrics" >&2
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    echo "WARN: curl не найден — пропуск metrics" >&2
    return 0
  fi
  local body
  body="$(curl -sf -H "X-API-Key: ${api_key}" \
    "${base}/discovery-api/parser/queue/metrics" 2>/dev/null)" || {
    echo "WARN: metrics недоступны по ${base}" >&2
    return 0
  }
  echo "$body" | head -c 2000
  echo ""
  if command -v jq >/dev/null 2>&1; then
    local done5 stuck
    done5="$(echo "$body" | jq -r '.done_last_5_min // .queue.done_last_5_min // empty' 2>/dev/null || true)"
    stuck="$(echo "$body" | jq -r '.stuck_count // .queue.stuck_count // empty' 2>/dev/null || true)"
    echo "metrics: done_last_5_min=${done5:-?} stuck_count=${stuck:-?}"
  fi
}

main() {
  ensure_discovery_env
  require_env "QUEUE_DATABASE_URL"
  if [[ "$VERIFY_ONLY" == true ]]; then
    verify_logs
    verify_metrics
    exit 0
  fi
  stop_competing_containers
  recreate_discovery_api
  verify_logs
  verify_metrics
  echo ""
  echo "=== Co-located in-process worker pool применён ==="
  echo "Запущено 4 параллельных воркера (INPROCESS_WORKER_COUNT=4)."
  echo "Не запускайте: docker compose up -d queue-worker"
  echo "producer-balancer — только после CLUMP_SKIP_LISTENERS (follow-up)"
}

main "$@"
