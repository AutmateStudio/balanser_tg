#!/usr/bin/env bash
# Безопасный деплой discovery-api на vps-104: git pull + миграции БД + ребилд.
# Не трогает .env, standalone_discovery/data/, sessions/ (в .gitignore).
#
#   bash scripts/safe_deploy_discovery_vps104.sh
#   bash scripts/safe_deploy_discovery_vps104.sh --skip-pull
#   bash scripts/safe_deploy_discovery_vps104.sh --skip-migrate
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SD="${ROOT}/standalone_discovery"
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

copy_if_exists() {
  local src="$1" dst="$2"
  if [ -e "$src" ]; then
    cp -a "$src" "$dst"
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

echo "=== 2. Права на data/sessions (ДО бэкапа — иначе Permission denied) ==="
if command -v sudo >/dev/null 2>&1; then
  sudo chown -R "$(whoami):$(whoami)" "${SD}/data" "${SD}/sessions" 2>/dev/null || true
fi

echo "=== 3. Бэкап не-git данных ==="
mkdir -p "$BACKUP"
copy_if_exists "${SD}/.env" "${BACKUP}/.env"
copy_if_exists "${SD}/data" "${BACKUP}/data"
copy_if_exists "${SD}/sessions" "${BACKUP}/sessions"
echo "Бэкап: ${BACKUP}"

if [ "$SKIP_PULL" = false ]; then
  echo "=== 4. git pull ==="
  cd "$ROOT"
  git fetch origin
  git checkout main
  git pull origin main
else
  echo "=== 4. git pull пропущен (--skip-pull) ==="
fi

copy_if_exists "${BACKUP}/.env" "${SD}/.env"

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
sleep 10

echo "=== 8. Проверка ==="
bash "${ROOT}/scripts/verify_discovery_vps104.sh" || die "verify не прошёл"

echo ""
echo "OK: деплой завершён"
echo "Лог: $LOG"
echo "Бэкап: $BACKUP"
