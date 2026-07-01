"""Проверка миграций очереди после наката на vps-107.

Запуск (SSH-туннель + QUEUE_DATABASE_URL):
    python scripts/verify_migrations.py
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg


async def main() -> int:
    dsn = os.getenv("QUEUE_DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: задайте QUEUE_DATABASE_URL", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        task_queue = await conn.fetchval("SELECT to_regclass('public.task_queue')")
        accounts = await conn.fetchval("SELECT to_regclass('public.accounts')")
        users = await conn.fetchval("SELECT count(*) FROM users")
        channels = await conn.fetchval("SELECT count(*) FROM source_channels")
        threshold = await conn.fetchval(
            "SELECT min_available_resource_percent FROM task_types WHERE code = 'parser_add_channel'"
        )
        rph = await conn.fetchval(
            "SELECT rph_limit FROM resource_op_types WHERE code = 'channels.GetFullChannel'"
        )
        zero_eff = await conn.fetchval(
            """
            SELECT count(*) FROM resource_op_types
            WHERE is_enabled = true
              AND floor(rph_limit * (1 - reserve_percent / 100.0)) = 0
            """
        )
        await conn.fetchrow("SELECT * FROM v_queue_metrics LIMIT 1")

        print(f"task_queue: {task_queue}")
        print(f"accounts: {accounts}")
        print(f"users: {users}")
        print(f"channels: {channels}")
        print(f"parser_add_channel threshold % (A16=0): {threshold}")
        print(f"GetFullChannel rph_limit (A14=112): {rph}")
        print(f"op with effective_rph=0 (A13, want 0): {zero_eff}")
        print("v_queue_metrics: OK")

        ok = (
            task_queue is not None
            and accounts is not None
            and threshold == 0
            and rph == 112
            and zero_eff == 0
        )
        if ok:
            print("\nПроверка пройдена.")
            return 0
        print("\nЕсть расхождения — см. значения выше.", file=sys.stderr)
        return 1
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
