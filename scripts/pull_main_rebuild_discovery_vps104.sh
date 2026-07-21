#!/usr/bin/env bash
# Подтянуть main и пересобрать discovery-api на vps-104.
# НЕ трогает INPROCESS_WORKER_COUNT / .env (в отличие от safe_deploy_*).
#
#   bash scripts/pull_main_rebuild_discovery_vps104.sh
#   bash scripts/pull_main_rebuild_discovery_vps104.sh --skip-build   # только git
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SD="${ROOT}/standalone_discovery"
SKIP_BUILD=false

for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=true ;;
    -h|--help)
      echo "Usage: $0 [--skip-build]"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

die() { echo "ERROR: $*" >&2; exit 1; }

cd "$ROOT"

# remote: balanser_tg или origin
REMOTE=""
if git remote get-url balanser_tg >/dev/null 2>&1; then
  REMOTE=balanser_tg
elif git remote get-url origin >/dev/null 2>&1; then
  REMOTE=origin
else
  die "нет remote balanser_tg/origin"
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "main" ] || die "нужна ветка main (сейчас: $BRANCH). Сделайте: git checkout main"

# сохранить .env на всякий случай
if [ -f "$SD/.env" ]; then
  cp -a "$SD/.env" "$SD/.env.bak.$(date +%Y%m%d_%H%M%S)"
  echo "backup .env: сохранён рядом с .env.bak.*"
  echo "INPROCESS до pull:"
  grep -E '^(DISCOVERY_INPROCESS_WORKER|INPROCESS_WORKER_COUNT)=' "$SD/.env" || true
fi

echo ">>> git fetch $REMOTE main"
git fetch "$REMOTE" main
echo ">>> merge --ff-only"
git merge --ff-only "$REMOTE/main"
echo "HEAD=$(git rev-parse --short HEAD) $(git log -1 --oneline)"

if [ -f "$SD/.env" ]; then
  echo "INPROCESS после pull (не должно сброситься):"
  grep -E '^(DISCOVERY_INPROCESS_WORKER|INPROCESS_WORKER_COUNT)=' "$SD/.env" || true
fi

if [ "$SKIP_BUILD" = true ]; then
  echo "OK: только git (--skip-build). Пересоберите вручную при необходимости."
  exit 0
fi

cd "$SD"
echo ">>> vpn healthy?"
docker compose up -d vpn
for i in $(seq 1 20); do
  st=$(docker inspect -f '{{.State.Health.Status}}' standalone-discovery-vpn 2>/dev/null || echo none)
  echo "vpn=$st"
  [ "$st" = "healthy" ] && break
  sleep 3
done

# SQLite action_queue: если битый — отложить в сторону (иначе startup failed)
if [ -f data/action_queue.db ]; then
  if ! file data/action_queue.db 2>/dev/null | grep -qi 'sqlite'; then
    ts=$(date +%Y%m%d_%H%M%S)
    echo "WARN: action_queue.db не SQLite — убираю в .corrupt.$ts"
    mv data/action_queue.db "data/action_queue.db.corrupt.$ts"
    rm -f data/action_queue.db-journal data/action_queue.db-wal data/action_queue.db-shm
  fi
fi

echo ">>> build + recreate discovery-api"
docker compose build discovery-api
docker compose up -d --force-recreate --no-deps discovery-api

for i in $(seq 1 40); do
  st=$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}n{{end}}' standalone-discovery-api 2>/dev/null || echo missing)
  echo "$(date +%H:%M:%S) discovery=$st"
  echo "$st" | grep -q 'running/healthy' && break
  if echo "$st" | grep -q restarting; then
    docker logs --tail=15 standalone-discovery-api 2>&1 | tail -8
  fi
  sleep 3
done

docker logs --tail=40 standalone-discovery-api 2>&1 | grep -E 'worker pool запущен|startup failed|Uvicorn running|file is not a database' || true

PORT="$(grep ^DISCOVERY_APP_PORT= .env 2>/dev/null | cut -d= -f2- | tr -d '\r"' || true)"
PORT="${PORT:-8000}"
curl -sS -o /dev/null -w "health HTTP %{http_code}\n" "http://127.0.0.1:${PORT}/health" || true

echo "OK: pull+rebuild завершён. Публичный URL с этого хоста может давать 502 (hairpin) — проверяйте localhost."
