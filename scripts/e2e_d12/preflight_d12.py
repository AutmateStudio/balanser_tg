#!/usr/bin/env python3
"""D12 — preflight перед E2E на staging (D8 + B9 schema).

Использование:
  set -a && source scripts/e2e_d12/env.d12 && set +a
  python scripts/e2e_d12/preflight_d12.py

  make e2e-d12-preflight
  docker compose run --rm --env-file scripts/e2e_d12/env.d12 test \
    python scripts/e2e_d12/preflight_d12.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_E2E_DIR = Path(__file__).resolve().parent
if str(_E2E_DIR) not in sys.path:
    sys.path.insert(0, str(_E2E_DIR))

from e2e_lib import (  # noqa: E402
    check_b9_schema,
    check_discovery_api,
    check_e2e_env,
    check_pg_queue_basics,
)


async def _main_async() -> int:
    print("=== D12 Preflight (D8 cutover + B9 schema) ===\n")

    pg_ok, pg_msgs = await check_pg_queue_basics()
    for line in pg_msgs:
        print(f"  [{'OK' if pg_ok else 'FAIL'}] {line}")

    b9_ok, b9_msgs = await check_b9_schema()
    for line in b9_msgs:
        print(f"  [{'OK' if b9_ok else 'FAIL'}] {line}")

    api_ok, api_msg = check_discovery_api()
    print(f"  [{'OK' if api_ok else 'FAIL'}] {api_msg}")

    env_ok, env_msgs = check_e2e_env()
    for line in env_msgs:
        print(f"  [{'OK' if env_ok else 'FAIL'}] {line}")

    all_ok = pg_ok and b9_ok and api_ok and env_ok
    print()
    if all_ok:
        print("Preflight D12: готов к run_e2e_d12.py")
        return 0
    print("Preflight D12: есть блокеры — см. выше")
    return 1


def main() -> None:
    raise SystemExit(asyncio.run(_main_async()))


if __name__ == "__main__":
    main()
