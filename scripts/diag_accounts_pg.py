"""Диагностика accounts / RPH VIEW (тот же DSN что queue-worker и тесты).

На dev-сервере Postgres в docker-compose не запущен — БД на vps-100:

  docker compose run --rm test python scripts/diag_accounts_pg.py
"""
from __future__ import annotations

import asyncio
import os

from app_balance.queue import db


async def main() -> None:
    dsn = os.getenv("QUEUE_DATABASE_URL", "").strip()
    if not dsn:
        print("QUEUE_DATABASE_URL не задан в .env")
        return
    host_hint = dsn.split("@", 1)[1] if "@" in dsn else dsn
    print(f"DSN host: {host_hint}\n")

    await db.init_pool()

    async with db.acquire() as conn:
        print("=== accounts (PG) ===")
        for row in await conn.fetch(
            "SELECT id, session_name, status, is_enabled FROM accounts ORDER BY session_name"
        ):
            print(dict(row))

        print("\n=== without_resource (v_account_resource_summary) ===")
        for row in await conn.fetch(
            """
            SELECT session_name, account_status, exhausted_ops_count,
                   worst_available_percent
            FROM v_account_resource_summary
            WHERE any_op_exhausted
            ORDER BY session_name
            """
        ):
            print(dict(row))

        print("\n=== v_accounts_overview (карточки дашборда) ===")
        overview = await conn.fetchrow("SELECT * FROM v_accounts_overview")
        print(dict(overview) if overview else {})

        print("\n=== test_* accounts ===")
        for row in await conn.fetch(
            "SELECT id, session_name, status FROM accounts WHERE session_name LIKE 'test_%'"
        ):
            print(dict(row))

        n_test = await conn.fetchval(
            "SELECT COUNT(*) FROM accounts WHERE session_name LIKE 'test_%'"
        )
        if n_test == 0:
            print("(нет)")

    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
