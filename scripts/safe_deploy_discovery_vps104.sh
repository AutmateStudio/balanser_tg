#!/usr/bin/env bash
# Безопасный деплой discovery-api на vps-104: git pull + миграции БД + ребилд.
# Не трогает .env, standalone_discovery/data/, sessions/ (в .gitignore).
# parser_jobs.json бэкапится явно (часто root:root после контейнера).
#
#   bash scripts/safe_deploy_discovery_vps104.sh
#   bash scripts/safe_deploy_discovery_vps104.sh --skip-pull
#   bash scripts/safe_deploy_discovery_vps104.sh --skip-migrate
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SD="${ROOT}/standalone_discovery"
PARSER_JOBS="${SD}/data/parser_jobs.json"
BACKUP="${HOME}/lidogen-deploy-backup-$(date +%Y%m%d-%H%M%S)"
SKIP_PULL=false
SKIP_MIGRATE=false
LOG="${HOME}/lidogen-deploy-$(date +%Y%m%d-%H%M%S).log"

for arg in "$@"; do
  case "$arg" in
    --skip-pull) SKIP_PULL=true ;;
    --skip-migrate) SKIP_MIGRATE=true ;;
    -h|--help)
      echo "Usage: $0 [--skip-pull] [--skip-migrate]"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

exec > >(tee -a "$LOG") 2>&1
echo "Лог деплоя: $LOG"

die() { echo "ERROR: $*" >&2; exit 1; }

file_size() {
  local path="$1"
  if stat -c%s "$path" >/dev/null 2>&1; then
    stat -c%s "$path"
  else
    stat -f%z "$path"
  fi
}

path_exists() {
  local path="$1"
  [ -e "$path" ] || sudo test -e "$path"
}

fix_data_permissions() {
  echo "=== Права на data/sessions ==="
  if ! command -v sudo >/dev/null 2>&1; then
    die "нужен sudo: data/sessions часто root:root после discovery-api"
  fi
  sudo mkdir -p "${SD}/data" "${SD}/sessions"
  sudo chown -R "$(whoami):$(whoami)" "${SD}/data" "${SD}/sessions"
  sudo chmod -R u+rwX "${SD}/data" "${SD}/sessions"
}

verify_parser_jobs() {
  local path="$1"
  local label="${2:-parser_jobs.json}"
  [ -f "$path" ] || die "${label} не найден: ${path}"
  local size
  size="$(file_size "$path")"
  [ "$size" -gt 0 ] || die "${label} пуст (${size} bytes): ${path}"
  python3 -m json.tool "$path" >/dev/null \
    || die "${label} невалидный JSON: ${path}"
  echo "OK: ${label} — ${size} bytes"
}

copy_path() {
  local src="$1" dst="$2"
  if cp -a "$src" "$dst" 2>/dev/null; then
    return 0
  fi
  sudo cp -a "$src" "$dst"
  if [ -d "$dst" ]; then
    sudo chown -R "$(whoami):$(whoami)" "$dst"
  else
    sudo chown "$(whoami):$(whoami)" "$dst"
  fi
}

backup_parser_jobs() {
  local dst="${BACKUP}/data/parser_jobs.json"
  mkdir -p "${BACKUP}/data"
  if ! path_exists "$PARSER_JOBS"; then
    echo "WARN: parser_jobs.json отсутствует — первый деплой или clump ещё не создавали"
    return 0
  fi
  copy_path "$PARSER_JOBS" "$dst"
  verify_parser_jobs "$dst" "бэкап parser_jobs.json"
}

backup_critical_data() {
  echo "=== Бэкап не-git данных ==="
  mkdir -p "$BACKUP"
  if [ -f "${SD}/.env" ]; then
    cp -a "${SD}/.env" "${BACKUP}/.env"
  fi
  backup_parser_jobs
  if path_exists "${SD}/data"; then
    copy_path "${SD}/data" "${BACKUP}/data"
    if path_exists "${BACKUP}/data/parser_jobs.json"; then
      verify_parser_jobs "${BACKUP}/data/parser_jobs.json" "data/ в бэкапе"
    fi
  fi
  if path_exists "${SD}/sessions"; then
    copy_path "${SD}/sessions" "${BACKUP}/sessions"
  fi
  echo "Бэкап: ${BACKUP}"
}

restore_parser_jobs_if_needed() {
  local src="${BACKUP}/data/parser_jobs.json"
  if [ ! -f "$src" ]; then
    return 0
  fi
  mkdir -p "${SD}/data"
  local need=false
  if [ ! -f "$PARSER_JOBS" ]; then
    need=true
  elif [ ! -s "$PARSER_JOBS" ]; then
    need=true
  elif ! python3 -m json.tool "$PARSER_JOBS" >/dev/null 2>&1; then
    need=true
  fi
  if [ "$need" = true ]; then
    echo "WARN: восстанавливаем parser_jobs.json из бэкапа деплоя"
    copy_path "$src" "$PARSER_JOBS"
  fi
  if path_exists "$PARSER_JOBS"; then
    verify_parser_jobs "$PARSER_JOBS" "parser_jobs.json на хосте"
  fi
}

# Одна строка на ключ (убирает дубликаты после повторных деплоев).
set_env_line() {
  local line="$1"
  local key="${line%%=*}"
  local env_file="$2"
  local tmp
  tmp="$(mktemp)"
  grep -vE "^${key}=" "$env_file" > "$tmp" || true
  echo "$line" >> "$tmp"
  mv "$tmp" "$env_file"
}

echo "=== 1. Останов discovery (PG-очередь сохраняется) ==="
(cd "$SD" && docker compose stop discovery-api) 2>/dev/null || true

fix_data_permissions
backup_critical_data

if [ "$SKIP_PULL" = false ]; then
  echo "=== 4. git pull ==="
  cd "$ROOT"
  git fetch origin
  git checkout main
  git pull origin main --ff-only
else
  echo "=== 4. git pull пропущен (--skip-pull) ==="
fi

if [ -f "${BACKUP}/.env" ]; then
  cp -a "${BACKUP}/.env" "${SD}/.env"
fi

restore_parser_jobs_if_needed

echo "=== 5. Флаги .env ==="
ENV="${SD}/.env"
[ -f "$ENV" ] || die "нет файла ${ENV}"

set_env_line "DISCOVERY_APP_PORT=8100" "$ENV"
set_env_line "DISCOVERY_INPROCESS_WORKER=true" "$ENV"
set_env_line "WORKER_TASK_ADAPTER=clump" "$ENV"
set_env_line "USE_PG_QUEUE=true" "$ENV"
set_env_line "INPROCESS_WORKER_COUNT=4" "$ENV"
set_env_line "JOIN_PENDING_RETRY_SECONDS=1800" "$ENV"

echo "=== 5.5. Миграции БД (migrate_queue.sh) ==="
if [ "$SKIP_MIGRATE" = false ]; then
  MIGRATE_DSN="$(grep -E '^QUEUE_DATABASE_URL=' "$ENV" | tail -1 | cut -d= -f2- | tr -d '\r')"
  if [ -n "$MIGRATE_DSN" ]; then
    QUEUE_DATABASE_URL="$MIGRATE_DSN" bash "${ROOT}/scripts/migrate_queue.sh" \
      || die "migrate_queue.sh упал — деплой остановлен ДО пересборки/рестарта"
  else
    echo "WARN: QUEUE_DATABASE_URL не найден в ${ENV} — миграции пропущены"
  fi
else
  echo "Миграции пропущены (--skip-migrate)"
fi

echo "=== 6. Останов queue-worker ==="
(cd "$ROOT" && docker compose stop queue-worker) 2>/dev/null || true

echo "=== 7. Сборка и запуск ==="
cd "$ROOT"
docker build -f standalone_discovery/Dockerfile.pg-queue -t standalone-discovery-api:latest .

cd "$SD"
docker compose up -d --force-recreate discovery-api

# API за VPN-namespace часто готов позже Docker "Started" (health: starting).
echo "=== ожидание /health (до ~60с) ==="
READY=false
for _ in $(seq 1 12); do
  code="$(curl -sS --connect-timeout 2 --max-time 5 \
    -o /dev/null -w '%{http_code}' "http://127.0.0.1:8100/health" 2>/dev/null || true)"
  code="$(printf '%s' "$code" | tr -d '\r\n')"
  if [ "$code" = "200" ]; then
    READY=true
    echo "health OK"
    break
  fi
  sleep 5
done
[ "$READY" = true ] || echo "WARN: /health ещё не 200 — verify попробует ещё раз"

fix_data_permissions
restore_parser_jobs_if_needed

echo "=== 8. Проверка ==="
bash "${ROOT}/scripts/verify_discovery_vps104.sh" || die "verify не прошёл"

if path_exists "$PARSER_JOBS"; then
  docker exec standalone-discovery-api python3 -c "
import json, os
p='/app/discovery_api/data/parser_jobs.json'
if not os.path.isfile(p):
    raise SystemExit('parser_jobs.json отсутствует в контейнере')
n=len(json.load(open(p, encoding='utf-8')))
print(f'контейнер parser_jobs.json: {os.path.getsize(p)} bytes, records={n}')
" || die "parser_jobs.json в контейнере недоступен или битый"
fi

echo ""
echo "OK: деплой завершён"
echo "Лог: $LOG"
echo "Бэкап: $BACKUP"
