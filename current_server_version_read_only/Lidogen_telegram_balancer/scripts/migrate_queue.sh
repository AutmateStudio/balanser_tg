#!/usr/bin/env bash
# =============================================================================
# A11 — миграционный runner PG Queue Balancer (ТЗ §28, план A11)
# «Одна команда применяет все миграции и seed.»
# =============================================================================
# Применяет DDL очереди + seed к PostgreSQL, отслеживая накат в
# public._migrations_applied (таблица из Lidogen_main_DB).
#
# Режимы:
#   integrate  — поверх существующей Lidogen_main_DB (A8_integrate_main_db.sql),
#                source_channels расширяется, не пересоздаётся;
#   greenfield — чистая БД (BD_schema.sql создаёт все таблицы, включая platforms).
# По умолчанию режим определяется автоматически по наличию platforms+source_channels.
#
# Использование:
#   QUEUE_DATABASE_URL=postgres://user:pass@host:5432/db ./scripts/migrate_queue.sh
#   ./scripts/migrate_queue.sh --dsn "postgres://..." --mode integrate
#   ./scripts/migrate_queue.sh --dry-run
#   ./scripts/migrate_queue.sh --no-seed
#
# Безопасность: каждый файл применяется в ОДНОЙ транзакции (--single-transaction)
# с ON_ERROR_STOP=1. Любая ошибка → полный откат файла, накат не отмечается.
# Перед запуском на проде сделайте бэкап — см. docs(plan)/db-safe-migration-guide.md.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_DIR="$(cd "$SCRIPT_DIR/../DB" && pwd)"

DSN="${QUEUE_DATABASE_URL:-}"
MODE="auto"
RUN_SEED=1
DRY_RUN=0

log()  { printf '[migrate-queue] %s\n' "$*"; }
err()  { printf '[migrate-queue][ОШИБКА] %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dsn)      DSN="$2"; shift 2 ;;
    --mode)     MODE="$2"; shift 2 ;;
    --no-seed)  RUN_SEED=0; shift ;;
    --dry-run)  DRY_RUN=1; shift ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done

command -v psql >/dev/null 2>&1 || die "psql не найден в PATH."
[[ -n "$DSN" ]] || die "Не задан DSN. Укажите QUEUE_DATABASE_URL или --dsn."

PSQL=(psql "$DSN" -v ON_ERROR_STOP=1 --quiet --no-psqlrc)

# --- Проверка подключения ----------------------------------------------------
"${PSQL[@]}" -tAc "SELECT 1;" >/dev/null 2>&1 || die "Не удалось подключиться к БД по DSN."
log "Подключение к БД успешно."

# --- Определение режима ------------------------------------------------------
if [[ "$MODE" == "auto" ]]; then
  HAS_MAIN="$("${PSQL[@]}" -tAc \
    "SELECT (to_regclass('public.source_channels') IS NOT NULL AND to_regclass('public.platforms') IS NOT NULL);")"
  if [[ "$HAS_MAIN" == "t" ]]; then MODE="integrate"; else MODE="greenfield"; fi
  log "Режим определён автоматически: $MODE"
else
  log "Режим задан вручную: $MODE"
fi

case "$MODE" in
  integrate)  SCHEMA_FILE="$DB_DIR/A8_integrate_main_db.sql" ;;
  greenfield) SCHEMA_FILE="$DB_DIR/BD_schema.sql" ;;
  *) die "Недопустимый режим: $MODE (ожидается integrate|greenfield|auto)" ;;
esac
SEED_FILE="$DB_DIR/A9_seed.sql"

[[ -f "$SCHEMA_FILE" ]] || die "Файл схемы не найден: $SCHEMA_FILE"
[[ -f "$SEED_FILE"   ]] || die "Файл seed не найден: $SEED_FILE"

# --- Таблица учёта миграций --------------------------------------------------
ensure_ledger() {
  "${PSQL[@]}" -c "CREATE TABLE IF NOT EXISTS public._migrations_applied (
    name varchar(255) PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
  );" >/dev/null
}

is_applied() {
  local name="$1" res
  res="$("${PSQL[@]}" -tAc "SELECT 1 FROM public._migrations_applied WHERE name = '$name' LIMIT 1;")"
  [[ "$res" == "1" ]]
}

# Apply-once: схема накатывается один раз, отмечается в журнале (атомарно с DDL).
apply_once() {
  local file="$1" base; base="$(basename "$file")"
  if is_applied "$base"; then log "пропуск (уже применён): $base"; return; fi
  if [[ "$DRY_RUN" == "1" ]]; then log "DRY-RUN применил бы: $base"; return; fi
  log "применение: $base"
  "${PSQL[@]}" --single-transaction \
    -f "$file" \
    -c "INSERT INTO public._migrations_applied(name) VALUES ('$base') ON CONFLICT (name) DO NOTHING;"
  log "ОК: $base"
}

# Seed идемпотентен (ON CONFLICT) — применяем всегда, обновляем applied_at.
apply_seed() {
  local file="$1" base; base="$(basename "$file")"
  if [[ "$DRY_RUN" == "1" ]]; then log "DRY-RUN применил бы seed: $base"; return; fi
  log "seed: $base"
  "${PSQL[@]}" --single-transaction \
    -f "$file" \
    -c "INSERT INTO public._migrations_applied(name) VALUES ('$base')
        ON CONFLICT (name) DO UPDATE SET applied_at = now();"
  log "ОК: $base"
}

# --- Выполнение --------------------------------------------------------------
A10_FILE="$DB_DIR/A10_attempt_status_running.sql"

ensure_ledger
apply_once "$SCHEMA_FILE"
[[ -f "$A10_FILE" ]] || die "Файл миграции не найден: $A10_FILE"
apply_once "$A10_FILE"
if [[ "$RUN_SEED" == "1" ]]; then
  apply_seed "$SEED_FILE"
else
  log "seed пропущен (--no-seed)."
fi

log "Готово. Применённые миграции:"
"${PSQL[@]}" -c "SELECT name, applied_at FROM public._migrations_applied ORDER BY applied_at;"
