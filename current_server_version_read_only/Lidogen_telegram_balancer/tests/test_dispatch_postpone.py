"""C3 — integration-тесты postpone при недоступном аккаунте (dispatch + worker + PG).

Критерии приёмки:
- 2 задачи, 1 свободный аккаунт: первая postpone (busy fixed account), вторая done.
- Нет свободных аккаунтов: pick_and_reserve → postpone, attempt_count не растёт.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from app_balance.queue import db
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from app_balance.queue_worker import QueueWorker, WorkerConfig
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_c3_postpone_"
# Низкий priority — holder-задачи не перехватывают claim у тестовых задач.
_HOLDER_PRIORITY = -2_000_000_000


@pytest.fixture
async def clean_queue(pg_pool):
    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_PREFIX}%",
            session_name_like=f"{_PREFIX}%",
        )

    await _cleanup()
    yield
    await _cleanup()


async def _enqueue(
    *,
    priority: int,
    account_id: int | None = None,
) -> int:
    res = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=f"{_PREFIX}{uuid.uuid4().hex}",
            priority=priority,
            account_id=account_id,
        )
    )
    assert res.created and res.task_id is not None
    return res.task_id


async def _insert_account(*, session_suffix: str) -> int:
    session_name = f"{_PREFIX}{session_suffix}_{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO accounts (session_name, status, is_enabled)
            VALUES ($1, 'active', true)
            RETURNING id
            """,
            session_name,
        )


async def _occupy_account(account_id: int) -> int:
    """Резервирует аккаунт под реальную строку task_queue (FK current_task_id)."""
    holder_task_id = await _enqueue(priority=_HOLDER_PRIORITY)
    async with db.acquire() as conn:
        reserved = await conn.fetchval(
            """
            UPDATE accounts
            SET current_task_id = $2, last_used_at = now()
            WHERE id = $1 AND current_task_id IS NULL
            RETURNING id
            """,
            account_id,
            holder_task_id,
        )
    assert reserved is not None, f"account {account_id} already busy"
    return holder_task_id


async def _lock_all_free_accounts() -> list[tuple[int, int]]:
    """Временно занимает все свободные аккаунты; возвращает (account_id, holder_task_id)."""
    locked: list[tuple[int, int]] = []
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id FROM accounts
            WHERE status IN ('active', 'cooldown')
              AND is_enabled = true
              AND current_task_id IS NULL
              AND (cooldown_until IS NULL OR cooldown_until <= now())
            """
        )
    for row in rows:
        holder_task_id = await _occupy_account(row["id"])
        locked.append((row["id"], holder_task_id))
    return locked


async def _unlock_accounts(locked: list[tuple[int, int]]) -> None:
    for account_id, holder_task_id in locked:
        async with db.acquire() as conn:
            await conn.execute(
                """
                UPDATE accounts
                SET current_task_id = NULL
                WHERE id = $1 AND current_task_id = $2
                """,
                account_id,
                holder_task_id,
            )
            await conn.execute(
                """
                DELETE FROM task_queue
                WHERE id = $1 AND dedup_key LIKE $2
                """,
                holder_task_id,
                f"{_PREFIX}%",
            )


async def _wait_until(predicate, *, timeout: float, interval: float = 0.02) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("condition not met")


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_busy_fixed_account_postpones_second_task_done(clean_queue) -> None:
    """2 задачи, 1 свободный аккаунт: первая postpone, вторая done."""
    busy_account_id = await _insert_account(session_suffix="busy")
    await _occupy_account(busy_account_id)
    free_account_id = await _insert_account(session_suffix="free")

    task1_id = await _enqueue(
        priority=2_000_000_001,
        account_id=busy_account_id,
    )
    task2_id = await _enqueue(priority=2_000_000_000)

    async with db.acquire() as conn:
        task1_attempt = await conn.fetchval(
            "SELECT attempt_count FROM task_queue WHERE id = $1", task1_id
        )

    worker = QueueWorker(
        WorkerConfig(
            worker_id="c3-it-worker",
            poll_interval_seconds=0.01,
            task_type_codes=["parser_add_channel"],
        )
    )

    run_task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(
            _wait_until(lambda: worker.processed >= 1, timeout=5.0),
            timeout=5.0,
        )
    finally:
        worker.stop()
        await asyncio.wait_for(run_task, timeout=5.0)

    async with db.acquire() as conn:
        task1 = await conn.fetchrow(
            """
            SELECT status, postpone_count, attempt_count, last_error,
                   run_after, locked_by, locked_until
            FROM task_queue WHERE id = $1
            """,
            task1_id,
        )
        task2 = await conn.fetchrow(
            "SELECT status, account_id FROM task_queue WHERE id = $1",
            task2_id,
        )
        free_current = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1",
            free_account_id,
        )
        usage_count = await conn.fetchval(
            "SELECT COUNT(*) FROM account_resource_usage WHERE task_id = $1",
            task1_id,
        )

    assert task1["status"] == "scheduled"
    assert task1["postpone_count"] == 1
    assert task1["attempt_count"] == task1_attempt
    assert "account_reserve_failed" in (task1["last_error"] or "")
    assert task1["run_after"] > datetime.now(timezone.utc)
    assert task1["locked_by"] is None
    assert task1["locked_until"] is None

    assert task2["status"] == "done"
    assert task2["account_id"] == free_account_id
    assert free_current is None
    assert usage_count == 0


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_no_available_account_postpones(clean_queue) -> None:
    """pick_and_reserve → None: postpone без execute, attempt_count не меняется."""
    locked = await _lock_all_free_accounts()
    try:
        task_id = await _enqueue(priority=2_000_000_000)

        async with db.acquire() as conn:
            attempt_before = await conn.fetchval(
                "SELECT attempt_count FROM task_queue WHERE id = $1", task_id
            )

        worker = QueueWorker(
            WorkerConfig(
                worker_id="c3-no-acc-worker",
                poll_interval_seconds=0.01,
                task_type_codes=["parser_add_channel"],
            )
        )

        run_task = asyncio.create_task(worker.run())
        try:
            await asyncio.wait_for(_wait_task_scheduled(task_id, timeout=5.0), timeout=5.0)
        finally:
            worker.stop()
            await asyncio.wait_for(run_task, timeout=5.0)

        async with db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT status, postpone_count, attempt_count, last_error
                FROM task_queue WHERE id = $1
                """,
                task_id,
            )

        assert row["status"] == "scheduled"
        assert row["postpone_count"] == 1
        assert row["attempt_count"] == attempt_before
        assert row["last_error"] == "no_available_account"
    finally:
        await _unlock_accounts(locked)


async def _wait_task_scheduled(task_id: int, *, timeout: float) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with db.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM task_queue WHERE id = $1", task_id
            )
        if status == "scheduled":
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(f"task {task_id} not scheduled within {timeout}s")
