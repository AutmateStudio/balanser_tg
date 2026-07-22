"""Диагностика: почему воркер не подхватывает задачи.

Выводит:
1. Состояние задач в очереди (pending/in_progress/postponed/stuck)
2. RPH-лимиты и текущий расход по каждому аккаунту
3. Последние postpone-причины

Запуск (из корня репо):
    docker compose run --rm test python scripts/diag_worker_rph.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


async def main() -> None:
    from app_balance.queue import db

    url = os.environ.get("QUEUE_DATABASE_URL", "")
    if not url:
        print("ERROR: QUEUE_DATABASE_URL не задан", file=sys.stderr)
        sys.exit(1)

    await db.init_pool()
    try:
        async with db.acquire() as conn:
            # ── 1. Статусы задач ──────────────────────────────────────────────
            rows = await conn.fetch(
                """
                SELECT status, COUNT(*) AS cnt,
                       MIN(run_after) AS earliest_run_after
                FROM task_queue
                GROUP BY status
                ORDER BY cnt DESC
                """
            )
            print("\n=== Состояние очереди ===")
            if rows:
                for r in rows:
                    ra = r["earliest_run_after"]
                    ra_str = ra.strftime("%H:%M:%S") if ra else "—"
                    print(f"  {r['status']:15s}  {r['cnt']:5d}  earliest_run_after={ra_str}")
            else:
                print("  (очередь пуста)")

            # ── 2. Последние postpone-причины ────────────────────────────────
            rows = await conn.fetch(
                """
                SELECT last_error, COUNT(*) AS cnt
                FROM task_queue
                WHERE status IN ('scheduled', 'retry')
                  AND last_error IS NOT NULL
                GROUP BY last_error
                ORDER BY cnt DESC
                LIMIT 10
                """
            )
            print("\n=== Причины postpone / retry (top-10) ===")
            if rows:
                for r in rows:
                    print(f"  {r['cnt']:5d}x  {r['last_error']}")
            else:
                print("  нет задач со статусом scheduled/retry")

            # ── 3. Pickable-аккаунты (сводка) ────────────────────────────────
            pickable = await conn.fetchval(
                """
                SELECT COUNT(*) FROM accounts
                WHERE status IN ('active', 'cooldown')
                  AND is_enabled = true
                  AND current_task_id IS NULL
                  AND (cooldown_until IS NULL OR cooldown_until <= now())
                """
            )
            total = await conn.fetchval("SELECT COUNT(*) FROM accounts")
            print(f"\n=== Аккаунты pickable: {pickable} / {total} ===")

            # ── 4. RPH-лимиты по аккаунтам ───────────────────────────────────
            rows = await conn.fetch(
                """
                SELECT
                    a.session_name,
                    a.status,
                    a.is_enabled,
                    a.current_task_id,
                    a.cooldown_until,
                    v.op_code,
                    v.effective_rph,
                    v.used_last_hour,
                    v.available_resource,
                    ROUND(v.available_resource_percent::numeric, 1) AS avail_pct
                FROM accounts a
                LEFT JOIN v_account_op_usage_last_hour v ON v.account_id = a.id
                ORDER BY a.session_name, v.op_code
                """
            )
            print("\n=== RPH по аккаунтам ===")
            if rows:
                for r in rows:
                    cu = r["cooldown_until"]
                    cu_str = cu.strftime("%H:%M:%S UTC") if cu else "—"
                    tid = r["current_task_id"] or "—"
                    op = r["op_code"] or "(нет op-данных)"
                    rph = r["effective_rph"]
                    used = r["used_last_hour"]
                    avail = r["avail_pct"]
                    print(
                        f"  {r['session_name']:20s}  status={r['status']:10s}"
                        f"  enabled={r['is_enabled']}  task={tid}"
                        f"  cooldown={cu_str}"
                        f"  op={op}  rph_limit={rph}  used={used}  avail={avail}%"
                    )
            else:
                print("  аккаунты не найдены")

            # ── 5. Глобальные настройки task_types ───────────────────────────
            rows = await conn.fetch(
                """
                SELECT
                    tt.code,
                    tt.is_enabled,
                    tt.min_available_resource_percent,
                    tt.retry_delay_seconds,
                    tt.max_attempts,
                    COUNT(tto.id) FILTER (WHERE rot.is_enabled) AS ops_count
                FROM task_types tt
                LEFT JOIN task_type_ops tto ON tto.task_type_id = tt.id
                LEFT JOIN resource_op_types rot ON rot.id = tto.op_type_id
                GROUP BY tt.id
                ORDER BY tt.code
                """
            )
            print("\n=== task_types ===")
            env_threshold = os.environ.get("RESOURCE_MIN_AVAILABLE_PERCENT", "")
            if env_threshold:
                print(f"  [env] RESOURCE_MIN_AVAILABLE_PERCENT={env_threshold} (override)")
            for r in rows:
                threshold = int(env_threshold) if env_threshold.isdigit() else r["min_available_resource_percent"]
                print(
                    f"  {r['code']:30s}  enabled={r['is_enabled']}"
                    f"  ops={r['ops_count']}"
                    f"  threshold={threshold}%"
                    f"  max_attempts={r['max_attempts']}"
                    f"  retry_delay={r['retry_delay_seconds']}s"
                )

            # ── 6. get_entity — узкое место для parser_add_channel ─────────────
            threshold_row = await conn.fetchrow(
                """
                SELECT min_available_resource_percent
                FROM task_types WHERE code = 'parser_add_channel'
                """
            )
            db_threshold = int(threshold_row["min_available_resource_percent"]) if threshold_row else 80
            env_threshold = os.environ.get("RESOURCE_MIN_AVAILABLE_PERCENT", "").strip()
            threshold = int(env_threshold) if env_threshold.isdigit() else db_threshold

            blocked = await conn.fetch(
                """
                SELECT a.session_name,
                       ROUND(v.available_resource_percent::numeric, 1) AS avail_pct
                FROM accounts a
                JOIN v_account_op_usage_last_hour v
                  ON v.account_id = a.id AND v.op_code = 'get_entity'
                WHERE a.status IN ('active', 'cooldown')
                  AND a.is_enabled = true
                  AND a.current_task_id IS NULL
                  AND (a.cooldown_until IS NULL OR a.cooldown_until <= now())
                  AND v.available_resource_percent < $1
                ORDER BY a.session_name
                """,
                threshold,
            )
            if blocked:
                print(
                    f"\n=== get_entity ниже порога {threshold}% "
                    f"(parser_add_channel не возьмёт эти аккаунты) ==="
                )
                for r in blocked:
                    print(f"  {r['session_name']:20s}  avail={r['avail_pct']}%")
                print(
                    f"\n⚠  Все pickable-аккаунты заблокированы по get_entity "
                    f"(avail < {threshold}%). Задачи уходят в postpone "
                    f"insufficient_resource:*:get_entity."
                )
                print(
                    "   Быстрый фикс: RESOURCE_MIN_AVAILABLE_PERCENT=50 "
                    "в standalone_discovery/.env → docker compose up -d --force-recreate discovery-api"
                )
                print(
                    "   Или подождите ~1 ч — used_last_hour для get_entity сбросится."
                )

            # ── 7. Совет ─────────────────────────────────────────────────────
            postponed = await conn.fetchval(
                "SELECT COUNT(*) FROM task_queue WHERE status = 'scheduled'"
            )
            if postponed > 0:
                print(
                    f"\n⚠  {postponed} задач в scheduled. Если причина INSUFFICIENT_RESOURCE"
                    " — RPH исчерпан. Подождите 1 час или установите"
                    " RESOURCE_MIN_AVAILABLE_PERCENT=0 в .env и пересоздайте discovery-api."
                )

    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
