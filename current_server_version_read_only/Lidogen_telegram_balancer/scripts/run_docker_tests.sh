#!/usr/bin/env bash
# Полный pytest в Docker (dev/staging через Tailscale).
# Вызывается сервисом docker compose run --rm test
set -eu

cd /app
export PYTHONPATH="${PYTHONPATH:-/app}"

echo "=== Preflight PostgreSQL ==="
python scripts/preflight_test_db.py

echo "=== pytest (tests/ + standalone_discovery/tests/) ==="
exec python -m pytest tests/ standalone_discovery/tests/ -v --tb=short "$@"
