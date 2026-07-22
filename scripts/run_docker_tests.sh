#!/usr/bin/env bash
# Полный pytest в Docker (dev/staging через Tailscale).
# Вызывается сервисом docker compose run --rm test
set -eu

cd /app
export PYTHONPATH="${PYTHONPATH:-/app}"

# pytest НИКОГДА не должен использовать реальный Telethon-адаптер (clump):
# воркеры, собранные в тестах через build_default_dispatcher, иначе падают на
# реальном execute (пустой payload → invalid_payload → задача не done → TimeoutError).
# .env на сервере выставляет WORKER_TASK_ADAPTER=clump для боевого воркера — здесь форсим mock.
export WORKER_TASK_ADAPTER=mock

# Напоминание: integration на shared PG требует, чтобы НИКТО не claim-ил задачи.
# Источников два: отдельный queue-worker и in-process worker внутри discovery-api.
# Guard в tests/conftest.py сам сделает probe-проверку и прервёт прогон, если
# обнаружит конкурента. PYTEST_DB_ISOLATED=1 отключает проверку (локальная PG).
if [[ -n "${QUEUE_DATABASE_URL:-}" && "${PYTEST_DB_ISOLATED:-}" != "1" ]]; then
  echo "i  Перед integration на shared PG остановите claimer'ы:"
  echo "i    docker compose stop queue-worker"
  echo "i    docker stop standalone-discovery-api"
  echo "i  Проще всего: make docker-test-safe (guard всё равно проверит probe)."
fi

echo "=== Сверка ops_catalog ↔ seed (E7) ==="
python scripts/verify_ops_catalog_seed.py

echo "=== Preflight PostgreSQL ==="
python scripts/preflight_test_db.py

echo "=== pytest (tests/ + standalone_discovery/tests/) ==="
exec python -m pytest tests/ standalone_discovery/tests/ -v --tb=short "$@"
