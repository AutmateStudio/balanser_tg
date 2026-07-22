#!/usr/bin/env bash
# Полный pull с GitHub на dev-сервер (vps-101) с бэкапом локальных правок
# и восстановлением серверных конфигов (.env, compose, Dockerfile).
#
# Использование:
#   ./scripts/deploy_pull_preserve_env.sh feat/g-wave5-finalize-monitoring
#   ./scripts/deploy_pull_preserve_env.sh origin/main
#
# Не удаляет untracked (.env, sessions/) — только сбрасывает tracked-изменения.

set -euo pipefail

BRANCH="${1:-feat/g-wave5-finalize-monitoring}"
REMOTE="${2:-origin}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${HOME}/backups/lidogen-balancer-${TS}"
CONFIG_DIR="${BACKUP_DIR}/server-config"
PATCH_DIR="${BACKUP_DIR}/local-changes"

KEEP=(
  .env
  docker-compose.yml
  Dockerfile
  standalone_discovery/.env
  standalone_discovery/docker-compose.yml
  standalone_discovery/Dockerfile
  standalone_discovery/Dockerfile.pg-queue
  scripts/e2e_d12/env.d12
  scripts/e2e_d9/env.d9
)

log() { printf '[deploy-pull] %s\n' "$*"; }
die() { printf '[deploy-pull] ERROR: %s\n' "$*" >&2; exit 1; }

mkdir -p "$CONFIG_DIR" "$PATCH_DIR"

log "Бэкап → $BACKUP_DIR"

git rev-parse --abbrev-ref HEAD >"$BACKUP_DIR/git-branch.txt" 2>/dev/null || true
git rev-parse HEAD >"$BACKUP_DIR/git-head.txt" 2>/dev/null || true
git status --porcelain=v1 >"$BACKUP_DIR/git-status.txt" || true
git diff >"$PATCH_DIR/worktree.patch" 2>/dev/null || true
git diff --cached >"$PATCH_DIR/staged.patch" 2>/dev/null || true

while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  status="${line:0:2}"
  path="${line:3}"
  [[ "$path" == *" -> "* ]] && path="${path%% -> *}"
  case "$status" in
    \?\?|!!) continue ;;  # untracked / ignored — не копируем ( .env, sessions )
  esac
  if [[ -f "$path" ]]; then
    mkdir -p "$PATCH_DIR/$(dirname "$path")"
    cp -a "$path" "$PATCH_DIR/$path"
  fi
done <"$BACKUP_DIR/git-status.txt"

for f in "${KEEP[@]}"; do
  if [[ -f "$f" ]]; then
    mkdir -p "$CONFIG_DIR/$(dirname "$f")"
    cp -a "$f" "$CONFIG_DIR/$f"
    log "  config: $f"
  fi
done

log "Сброс локальных правок tracked-файлов (untracked не трогаем)…"
git reset --hard HEAD

log "Fetch $REMOTE…"
git fetch "$REMOTE"

if git show-ref --verify --quiet "refs/remotes/${REMOTE}/${BRANCH#origin/}"; then
  REF="${REMOTE}/${BRANCH#origin/}"
elif git show-ref --verify --quiet "refs/remotes/${REMOTE}/${BRANCH}"; then
  REF="${REMOTE}/${BRANCH}"
else
  die "ветка ${BRANCH} не найдена на ${REMOTE}. git branch -r | grep ${BRANCH}"
fi

if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  git checkout "$BRANCH"
else
  git checkout -b "$BRANCH" "$REF"
fi

git pull --ff-only "$REMOTE" "${BRANCH#origin/}" || git merge --ff-only "$REF"

log "Восстановление серверных конфигов…"
for f in "${KEEP[@]}"; do
  if [[ -f "$CONFIG_DIR/$f" ]]; then
    cp -a "$CONFIG_DIR/$f" "$f"
    log "  restored: $f"
  fi
done

log "Готово."
log "  ветка:  $(git rev-parse --abbrev-ref HEAD)"
log "  commit: $(git log -1 --oneline)"
log "  бэкап:  $BACKUP_DIR"
log ""
log "Дальше на сервере:"
log "  docker compose stop queue-worker"
log "  docker stop standalone-discovery-api 2>/dev/null || true"
log "  docker compose build test"
log "  docker compose run --rm migrate"
log "  docker compose run --rm test python scripts/preflight_test_db.py"
log "  make docker-test-g && make docker-test-safe"
log "  docker compose up -d queue-worker"
log "  docker start standalone-discovery-api 2>/dev/null || true"
