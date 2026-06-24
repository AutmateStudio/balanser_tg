#!/usr/bin/env bash
# Полный pytest на хосте Linux: tests/ + standalone_discovery/tests/
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="${PYTHONPATH:-$ROOT}"

if [[ -n "${QUEUE_DATABASE_URL:-}" ]]; then
  echo "=== Preflight PostgreSQL ==="
  python scripts/preflight_test_db.py
fi

exec python -m pytest tests/ standalone_discovery/tests/ -v --tb=short "$@"
