"""Общие фикстуры pytest."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app_balance.queue import db

_sd_root = Path(__file__).resolve().parents[1] / "standalone_discovery"
if _sd_root.is_dir() and str(_sd_root) not in sys.path:
    sys.path.insert(0, str(_sd_root))

# Высокий приоритет тестовых задач. ВНИМАНИЕ: это НЕ изолирует от фонового
# queue-worker — claim_next берёт MAX(priority) первым, поэтому работающий
# worker, наоборот, перехватывает именно тестовые задачи. На shared PG worker
# обязательно должен быть остановлен (см. guard ниже и QUEUE_WORKER_STOPPED).
TEST_ISOLATION_PRIORITY = int(os.getenv("PYTEST_TEST_PRIORITY", "2000000000"))


def _has_dsn() -> bool:
    return bool(os.getenv("QUEUE_DATABASE_URL", "").strip())


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


requires_pg = pytest.mark.skipif(
    not _has_dsn(),
    reason="QUEUE_DATABASE_URL не задан — интеграционные тесты пропущены",
)


async def _probe_competing_claimer() -> str | None:
    """Enqueue probe-задачу и проверяет, что её никто не claim-нул.

    Любой конкурирующий claimer (отдельный queue-worker ИЛИ in-process worker
    внутри discovery-api) перехватит probe-задачу: status станет != 'queued'
    или появится locked_by. Возвращает строку-описание при обнаружении, иначе
    None. Надёжнее env-флага, т.к. проверяет реальное состояние БД.
    """
    import asyncio
    import uuid

    from app_balance.queue import db
    from tests.pg_cleanup import cleanup_queue_test_data

    dedup = f"test_probe_{uuid.uuid4().hex}"
    await db.init_pool()
    try:
        async with db.acquire() as conn:
            task_type_id = await conn.fetchval(
                "SELECT id FROM task_types WHERE code = 'parser_add_channel'"
            )
            if task_type_id is None:
                return None
            task_id = await conn.fetchval(
                """
                INSERT INTO task_queue (
                    task_type_id, task_type_code, status, priority,
                    payload, dedup_key, max_attempts, attempt_count, run_after
                ) VALUES (
                    $1, 'parser_add_channel', 'queued', $2,
                    '{}'::jsonb, $3, 5, 0, now()
                )
                RETURNING id
                """,
                task_type_id,
                TEST_ISOLATION_PRIORITY,
                dedup,
            )

        detected: str | None = None
        for _ in range(15):  # ~3 секунды наблюдения
            await asyncio.sleep(0.2)
            async with db.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT status, locked_by FROM task_queue WHERE id = $1",
                    task_id,
                )
            if row is None:
                detected = "probe-задача исчезла (claim + complete сторонним worker)"
                break
            if row["status"] != "queued" or row["locked_by"] is not None:
                detected = (
                    f"probe-задача перехвачена: status={row['status']} "
                    f"locked_by={row['locked_by']}"
                )
                break

        await cleanup_queue_test_data(dedup_key_like="test_probe_%")
        return detected
    finally:
        await db.close_pool()


def pytest_collection_modifyitems(session, config, items) -> None:
    """Guard: integration на shared PG требует отсутствия конкурирующего claimer.

    Перед прогоном integration-тестов на реальной БД делаем активную probe-
    проверку: задачу не должен перехватить ни отдельный queue-worker, ни
    in-process worker внутри discovery-api. Конкурент ломает claim/dispatch и
    создаёт FK-гонки при очистке (см. tests/pg_cleanup.py).

    Отключить проверку (изолированная локальная PG): PYTEST_DB_ISOLATED=1.
    """
    if not _has_dsn():
        return
    if _is_truthy(os.getenv("PYTEST_DB_ISOLATED")):
        return
    has_integration = any(
        item.get_closest_marker("integration") is not None for item in items
    )
    if not has_integration:
        return

    import asyncio

    try:
        detected = asyncio.run(_probe_competing_claimer())
    except Exception:  # noqa: BLE001 — probe не должен валить сессию; preflight отдельно
        return
    if detected is None:
        return

    pytest.exit(
        "Обнаружен конкурирующий claimer на shared PG — прогон прерван.\n"
        f"  {detected}\n"
        "Остановите ВСЕ источники claim перед integration-тестами:\n"
        "  1) docker compose stop queue-worker\n"
        "  2) docker stop standalone-discovery-api   "
        "(in-process worker внутри discovery)\n"
        "  3) make docker-test-safe\n"
        "Для изолированной локальной PG задайте PYTEST_DB_ISOLATED=1.",
        returncode=2,
    )


@pytest.fixture
async def pg_pool():
    """Инициализированный пул; закрывается после теста."""
    await db.close_pool()
    await db.init_pool()
    yield
    await db.close_pool()