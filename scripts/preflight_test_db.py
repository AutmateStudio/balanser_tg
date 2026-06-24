#!/usr/bin/env python3
"""Проверка PG и схемы очереди перед pytest (Docker test / dev-сервер)."""
from __future__ import annotations

import asyncio
import os
import sys


def _dsn_host_hint() -> str:
    dsn = os.getenv("QUEUE_DATABASE_URL", "").strip()
    if "@" in dsn:
        return dsn.split("@", 1)[1]
    return "(host не указан)"


async def _check() -> int:
    if not os.getenv("QUEUE_DATABASE_URL", "").strip():
        print(
            "QUEUE_DATABASE_URL не задан. "
            "На dev-сервере: cp .env.example .env и укажите DSN к vps-100.",
            file=sys.stderr,
        )
        return 1

    from app_balance.queue import db

    try:
        await db.init_pool()
        if not await db.healthcheck():
            print(
                f"healthcheck() вернул false для {_dsn_host_hint()}",
                file=sys.stderr,
            )
            return 1

        async with db.acquire() as conn:
            task_types_count = await conn.fetchval("SELECT COUNT(*) FROM task_types")
            view_exists = await conn.fetchval(
                """
                SELECT 1 FROM information_schema.views
                WHERE table_schema = 'public' AND table_name = 'v_queue_metrics'
                """
            )

        if int(task_types_count or 0) == 0:
            print(
                "Таблица task_types пуста. "
                "Выполните: docker compose run --rm migrate",
                file=sys.stderr,
            )
            return 1

        if not view_exists:
            print(
                "VIEW v_queue_metrics не найден. "
                "Выполните: docker compose run --rm migrate",
                file=sys.stderr,
            )
            return 1

        print(
            f"Preflight OK: PostgreSQL {_dsn_host_hint()}, "
            f"task_types={task_types_count}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001 — диагностика перед pytest
        print(
            f"Не удалось подключиться к PostgreSQL ({_dsn_host_hint()}): {exc}",
            file=sys.stderr,
        )
        print(
            "На dev-сервере с Tailscale используйте network_mode: host у сервиса test "
            "или укажите Tailscale-IP vps-100 в QUEUE_DATABASE_URL.",
            file=sys.stderr,
        )
        return 1
    finally:
        await db.close_pool()


def main() -> None:
    raise SystemExit(asyncio.run(_check()))


if __name__ == "__main__":
    main()
