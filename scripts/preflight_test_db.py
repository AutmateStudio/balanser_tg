#!/usr/bin/env python3
"""Проверка PG и схемы очереди перед pytest (Docker test / dev-сервер)."""
from __future__ import annotations

import asyncio
import os
import sys

# Мониторинговые VIEW блока G (G1/G2, §26.2/26.3). Должны присутствовать после
# migrate и исполняться без ошибок (см. DB/BD_schema.sql, DB/A8_integrate_main_db.sql).
MONITORING_VIEWS: tuple[str, ...] = (
    "v_queue_size_by_status",
    "v_queue_size_by_type",
    "v_queue_metrics",
    "v_high_postpone_tasks",
    "v_account_op_usage_last_hour",
    "v_account_resource_summary",
    "v_accounts_overview",
    "v_account_error_rate_last_hour",
    "v_task_type_error_rate_last_hour",
    "v_channel_capacity_usage",
    "v_recurring_errors_window",
)


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
            existing_views = {
                row["table_name"]
                for row in await conn.fetch(
                    """
                    SELECT table_name FROM information_schema.views
                    WHERE table_schema = 'public' AND table_name = ANY($1::text[])
                    """,
                    list(MONITORING_VIEWS),
                )
            }

        if int(task_types_count or 0) == 0:
            print(
                "Таблица task_types пуста. "
                "Выполните: docker compose run --rm migrate",
                file=sys.stderr,
            )
            return 1

        missing = [v for v in MONITORING_VIEWS if v not in existing_views]
        if missing:
            print(
                "Не найдены мониторинговые VIEW блока G: "
                f"{', '.join(missing)}. "
                "Выполните: docker compose run --rm migrate",
                file=sys.stderr,
            )
            return 1

        # VIEW существует в каталоге, но может падать при исполнении (например,
        # рассинхрон со схемой таблиц). LIMIT 0 исполняет план без чтения строк.
        async with db.acquire() as conn:
            for view in MONITORING_VIEWS:
                try:
                    await conn.execute(f'SELECT * FROM "{view}" LIMIT 0')
                except Exception as exc:  # noqa: BLE001 — диагностика конкретного VIEW
                    print(
                        f"VIEW {view} не исполняется: {exc}. "
                        "Проверьте схему и повторите migrate.",
                        file=sys.stderr,
                    )
                    return 1

        print(
            f"Preflight OK: PostgreSQL {_dsn_host_hint()}, "
            f"task_types={task_types_count}, "
            f"monitoring_views={len(MONITORING_VIEWS)}/{len(MONITORING_VIEWS)}"
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
