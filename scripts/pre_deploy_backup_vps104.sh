#!/usr/bin/env bash
# Ручной бэкап перед деплоем (vps-104): .env + data/ + parser_jobs.json.
# Использует sudo для файлов root:root после discovery-api.
#
#   bash scripts/pre_deploy_backup_vps104.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SD="${ROOT}/standalone_discovery"
PARSER_JOBS="${SD}/data/parser_jobs.json"
BACKUP="${HOME}/lidogen-pre-deploy-backup-$(date +%Y%m%d-%H%M%S)"

die() { echo "ERROR: $*" >&2; exit 1; }

echo "=== Останов discovery (чтобы не писали в data во время копирования) ==="
(cd "$SD" && docker compose stop discovery-api) 2>/dev/null || true

echo "=== Права на data/sessions ==="
command -v sudo >/dev/null 2>&1 || die "нужен sudo"
sudo mkdir -p "${SD}/data" "${SD}/sessions"
sudo chown -R "$(whoami):$(whoami)" "${SD}/data" "${SD}/sessions"
sudo chmod -R u+rwX "${SD}/data" "${SD}/sessions"

mkdir -p "$BACKUP"

if [ -f "${SD}/.env" ]; then
  cp -a "${SD}/.env" "${BACKUP}/.env"
  echo "OK: .env"
else
  echo "WARN: .env не найден"
fi

if [ -f "$PARSER_JOBS" ] || sudo test -f "$PARSER_JOBS"; then
  mkdir -p "${BACKUP}/data"
  if ! cp -a "$PARSER_JOBS" "${BACKUP}/data/parser_jobs.json" 2>/dev/null; then
    sudo cp -a "$PARSER_JOBS" "${BACKUP}/data/parser_jobs.json"
    sudo chown "$(whoami):$(whoami)" "${BACKUP}/data/parser_jobs.json"
  fi
  python3 -m json.tool "${BACKUP}/data/parser_jobs.json" >/dev/null
  echo "OK: parser_jobs.json — $(wc -c < "${BACKUP}/data/parser_jobs.json") bytes"
else
  echo "WARN: parser_jobs.json отсутствует"
fi

if [ -d "${SD}/data" ]; then
  cp -a "${SD}/data" "${BACKUP}/data-full" 2>/dev/null \
    || { sudo cp -a "${SD}/data" "${BACKUP}/data-full" && sudo chown -R "$(whoami):$(whoami)" "${BACKUP}/data-full"; }
  echo "OK: data/"
fi

if [ -d "${SD}/sessions" ]; then
  cp -a "${SD}/sessions" "${BACKUP}/sessions" 2>/dev/null \
    || { sudo cp -a "${SD}/sessions" "${BACKUP}/sessions" && sudo chown -R "$(whoami):$(whoami)" "${BACKUP}/sessions"; }
  echo "OK: sessions/"
fi

echo ""
echo "Бэкап: $BACKUP"
echo "Для восстановления parser_jobs:"
echo "  cp -a ${BACKUP}/data/parser_jobs.json ${PARSER_JOBS}"
