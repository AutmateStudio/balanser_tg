"""Общие фикстуры для сценариев приёмки §30 ТЗ (tz-extract.txt).

Тесты используют только публичные точки входа (enqueue, worker, dispatch, PG-состояние)
и не завязаны на внутренние SQL/классы реализации.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.dispatch import TaskDispatcher
from app_balance.queue.errors import RetryableError
from app_balance.queue.mock_adapter import MockTaskAdapter
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.resource_check import ResourceChecker
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from app_balance.queue_worker import QueueWorker, WorkerConfig
from tests.conftest import requires_pg, TEST_ISOLATION_PRIORITY
from tests.pg_cleanup import cleanup_queue_test_data

PREFIX = "test_tz30_"
# Изоляция от фонового queue-worker и чужих задач на dev-базе.
TEST_PRIORITY = TEST_ISOLATION_PRIORITY
HOLDER_PRIORITY = -2_000_000_000
TASK_TYPE_ADD = "parser_add_channel"
TASK_TYPE_MOVE = "move_channel"
WORKER_TYPES = [TASK_TYPE_ADD]


def unique_key() -> str:
    return f"{PREFIX}{uuid.uuid4().hex}"


async def cleanup_tz30_rows() -> None:
    await cleanup_queue_test_data(
        dedup_key_like=f"{PREFIX}%",
        session_name_like=f"{PREFIX}%",
    )


@pytest.fixture
async def tz30_clean(pg_pool):
    await cleanup_tz30_rows()
    yield
    await cleanup_tz30_rows()


async def insert_account(*, suffix: str) -> int:
    session_name = f"{PREFIX}{suffix}_{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            """
            INSERT INTO accounts (session_name, status, is_enabled)
            VALUES ($1, 'active', true)
            RETURNING id
            """,
            session_name,
        )
    return int(account_id)


async def enqueue_task(
    *,
    task_type_code: str = TASK_TYPE_ADD,
    priority: int | None = TEST_PRIORITY,
    account_id: int | None = None,
    source_account_id: int | None = None,
    target_account_id: int | None = None,
    dedup_key: str | None = None,
    run_after: datetime | None = None,
    payload: dict | None = None,
) -> int:
    res = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code=task_type_code,
            dedup_key=dedup_key or unique_key(),
            priority=priority,
            account_id=account_id,
            source_account_id=source_account_id,
            target_account_id=target_account_id,
            run_after=run_after,
            payload=payload or {},
        )
    )
    assert res.created and res.task_id is not None
    return int(res.task_id)


async def occupy_account(account_id: int) -> int:
    holder_task_id = await enqueue_task(priority=HOLDER_PRIORITY)
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
    assert reserved is not None
    return holder_task_id


async def lock_all_free_accounts_except(exclude_ids: set[int]) -> list[tuple[int, int]]:
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
        account_id = int(row["id"])
        if account_id in exclude_ids:
            continue
        holder_task_id = await occupy_account(account_id)
        locked.append((account_id, holder_task_id))
    return locked


async def unlock_accounts(locked: list[tuple[int, int]]) -> None:
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
                f"{PREFIX}%",
            )


async def task_row(task_id: int):
    async with db.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT status, priority, task_type_code, account_id,
                   attempt_count, postpone_count, max_attempts,
                   last_error, run_after, locked_by, locked_until,
                   finished_at, started_at
            FROM task_queue WHERE id = $1
            """,
            task_id,
        )


async def task_status(task_id: int) -> str | None:
    async with db.acquire() as conn:
        return await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", task_id
        )


async def usage_count_for_task(task_id: int) -> int:
    async with db.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM account_resource_usage WHERE task_id = $1",
                task_id,
            )
            or 0
        )


async def task_attempts_for_task(task_id: int) -> list:
    """E4: все попытки задачи, отсортированные по attempt_number."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, attempt_number, status, error_code, finished_at
            FROM task_attempts
            WHERE task_id = $1
            ORDER BY attempt_number ASC
            """,
            task_id,
        )
    return list(rows)


async def assert_attempts_sync_with_queue(task_id: int) -> None:
    """E4: attempt_count в task_queue совпадает с числом строк в task_attempts."""
    row = await task_row(task_id)
    attempts = await task_attempts_for_task(task_id)
    attempt_count = int(row["attempt_count"])
    assert len(attempts) == attempt_count
    if attempts:
        numbers = [int(a["attempt_number"]) for a in attempts]
        assert numbers == list(range(1, attempt_count + 1))
        assert max(numbers) == attempt_count


async def wait_until(predicate, *, timeout: float, interval: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("условие не выполнено за отведённое время")


async def wait_task_status(task_id: int, expected: str, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await task_status(task_id)
        if status == expected:
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(f"задача {task_id} не перешла в статус {expected}")


def build_worker(
    *,
    worker_id: str = "tz30-worker",
    adapter: MockTaskAdapter | None = None,
    task_type_codes: list[str] | None = None,
) -> QueueWorker:
    config = WorkerConfig(
        worker_id=worker_id,
        poll_interval_seconds=0.01,
        task_type_codes=task_type_codes or WORKER_TYPES,
    )
    mock = adapter or MockTaskAdapter()
    dispatcher = TaskDispatcher(
        queue=TaskQueueRepo(),
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=mock,
        resource_check=ResourceChecker(ResourceUsageRepo()),
        postpone_delay_seconds=config.postpone_delay_seconds,
        retry_delay_seconds=config.retry_delay_seconds,
    )
    return QueueWorker(config, dispatcher=dispatcher)


async def run_worker_until(
    worker: QueueWorker,
    *,
    processed: int = 1,
    timeout: float = 5.0,
) -> None:
    run_task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(
            wait_until(lambda: worker.processed >= processed, timeout=timeout),
            timeout=timeout,
        )
    finally:
        worker.stop()
        await asyncio.wait_for(run_task, timeout=timeout)


async def run_worker_until_task_status(
    worker: QueueWorker,
    task_id: int,
    expected: str,
    *,
    timeout: float = 5.0,
) -> None:
    """Дождаться статуса задачи (retry/postpone/done). processed не растёт при retry."""
    run_task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(
            wait_task_status(task_id, expected, timeout=timeout),
            timeout=timeout,
        )
    finally:
        worker.stop()
        await asyncio.wait_for(run_task, timeout=timeout)


async def run_worker_until_attempt_count(
    worker: QueueWorker,
    task_id: int,
    expected: int,
    *,
    timeout: float = 10.0,
) -> None:
    """Дождаться attempt_count >= expected.

    При retry worker.processed не растёт и status может уже быть 'retry' с прошлой
    попытки — поэтому ориентируемся именно на счётчик попыток.
    """
    run_task = asyncio.create_task(worker.run())
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = await task_row(task_id)
            if row is not None and int(row["attempt_count"]) >= expected:
                return
            await asyncio.sleep(0.05)
        raise TimeoutError(
            f"задача {task_id} не достигла attempt_count={expected} за {timeout}s"
        )
    finally:
        worker.stop()
        await asyncio.wait_for(run_task, timeout=timeout)


class FailingTaskAdapter(MockTaskAdapter):
    """Публичный mock-адаптер: имитирует retryable-ошибку Telethon без реального RPC.

    E1: адаптеры обязаны бросать типизированные ошибки. message трактуется как
    стабильный error_code (например flood_wait / transient_error) — он попадает в
    task_queue.last_error и task_attempts.error_code.
    """

    def __init__(self, message: str = "transient_error") -> None:
        super().__init__()
        self.message = message

    async def execute(self, task, *, account) -> None:  # type: ignore[override]
        raise RetryableError(self.message, self.message)


def future_run_after(*, hours: float = 1.0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


pytestmark_requires_pg = requires_pg
