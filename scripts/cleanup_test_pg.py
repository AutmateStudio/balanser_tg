"""FK-safe очистка тестовых данных integration-тестов из shared PG.

Перед запуском остановите claimer'ы и продюсеры:

  docker compose stop queue-worker producer-collect producer-update producer-balancer
  docker stop standalone-discovery-api 2>/dev/null || true

Запуск:

  docker compose run --rm test python scripts/cleanup_test_pg.py

После:

  docker compose up -d queue-worker producer-collect producer-update producer-balancer
  docker start standalone-discovery-api 2>/dev/null || true
"""
from __future__ import annotations

import asyncio

from app_balance.queue import db
from tests.pg_cleanup import cleanup_queue_test_data

PREFIXES = [
    "test_b3_",
    "test_b4_",
    "test_b5_postpone_",
    "test_b6_",
    "test_b7_",
    "test_b8_",
    "test_b9_",
    "test_c1_",
    "test_c3_postpone_",
    "test_c4_dual_",
    "test_c5_",
    "test_c6_watchdog_",
    "test_c8_dispatch_",
    "test_c9_multi_",
    "test_d5_",
    "test_d7_",
    "test_d7_repo_",
    "test_d9_",
    "test_d10_",
    "test_e2_",
    "test_e3_",
    "test_e6_dispatch_",
    "test_e8_",
    "test_f4_collect_",
    "test_f7_update_",
    "test_g0_",
    "test_g3_",
    "test_g4_",
    "test_g5_",
    "test_g6_",
    "test_g7_",
    "test_int_",
    "test_tz30_",
    "test_a10_",
    "test_probe_",
]

PRODUCER_DEDUP_SQL = """
    dedup_key LIKE 'collect_extra_data:%'
    OR dedup_key LIKE 'update_channel:%'
    OR dedup_key LIKE 'move_channel:%'
"""


async def _cleanup_producer_tasks() -> int:
    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                f"""
                DELETE FROM task_attempts
                WHERE task_id IN (SELECT id FROM task_queue WHERE {PRODUCER_DEDUP_SQL})
                """
            )
            await conn.execute(
                f"""
                DELETE FROM account_resource_usage
                WHERE task_id IN (SELECT id FROM task_queue WHERE {PRODUCER_DEDUP_SQL})
                """
            )
            await conn.execute(
                f"""
                UPDATE accounts SET current_task_id = NULL
                WHERE current_task_id IN (
                    SELECT id FROM task_queue WHERE {PRODUCER_DEDUP_SQL}
                )
                """
            )
            await conn.execute(
                f"""
                UPDATE source_channels SET assigned_account_id = NULL
                WHERE assigned_account_id IN (
                    SELECT DISTINCT account_id FROM task_queue
                    WHERE account_id IS NOT NULL AND ({PRODUCER_DEDUP_SQL})
                )
                """
            )
            tag = await conn.execute(
                f"""
                DELETE FROM task_queue
                WHERE {PRODUCER_DEDUP_SQL}
                   OR created_by IN (
                       'collect_extra_data_producer', 'channel_balancer', 'update_channel_producer'
                   )
                """
            )
    return int(tag.split()[-1]) if tag else 0


async def _cleanup_f4_channels() -> None:
    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                DELETE FROM source_channels
                WHERE external_channel_id LIKE 'test_f4_collect_%'
                """
            )
            await conn.execute(
                """
                DELETE FROM platforms WHERE code LIKE 'test_f4_collect_%'
                """
            )


async def _cleanup_g6_adjustments() -> None:
    async with db.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM resource_limit_adjustments
            WHERE account_id IN (
                SELECT id FROM accounts WHERE session_name LIKE 'test_g6_%'
            )
            """
        )


async def main() -> None:
    await db.init_pool()
    producer_deleted = await _cleanup_producer_tasks()
    await _cleanup_f4_channels()
    await _cleanup_g6_adjustments()
    for prefix in PREFIXES:
        await cleanup_queue_test_data(
            dedup_key_like=f"{prefix}%",
            session_name_like=f"{prefix}%",
        )
    await db.close_pool()
    print(f"OK: producer tasks deleted={producer_deleted}, prefixes={len(PREFIXES)}")


if __name__ == "__main__":
    asyncio.run(main())
