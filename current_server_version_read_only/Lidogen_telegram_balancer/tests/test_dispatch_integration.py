"""C8 — mock-adapter integration test: enqueue → worker (1 итерация) → done (без Telethon)."""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.dispatch import TaskDispatcher
from app_balance.queue.mock_adapter import MockTaskAdapter
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.resource_check import ResourceChecker
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from app_balance.queue_worker import QueueWorker, WorkerConfig
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_c8_dispatch_"
_HOLDER_PRIORITY = -2_000_000_000
_TEST_PRIORITY = 2_000_000_000
_TEST_PAYLOAD = {"ref": "@c8_test"}


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
    priority: int = _TEST_PRIORITY,
    account_id: int | None = None,
    payload: dict | None = None,
) -> int:
    res = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=f"{_PREFIX}{uuid.uuid4().hex}",
            priority=priority,
            account_id=account_id,
            payload=payload or dict(_TEST_PAYLOAD),
        )
    )
    assert res.created and res.task_id is not None
    return res.task_id


async def _insert_account(*, session_suffix: str) -> tuple[int, str]:
    session_name = f"{_PREFIX}{session_suffix}_{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            """
            INSERT INTO accounts (session_name, status, is_enabled)
            VALUES ($1, 'active', true)
            RETURNING id
            """,
            session_name,
        )
    return int(account_id), session_name


async def _occupy_account(account_id: int) -> int:
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


async def _lock_all_free_accounts_except(exclude_ids: set[int]) -> list[tuple[int, int]]:
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
        holder_task_id = await _occupy_account(account_id)
        locked.append((account_id, holder_task_id))
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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("condition not met")


async def _task_status(task_id: int) -> str | None:
    async with db.acquire() as conn:
        return await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", task_id
        )


def _build_worker(*, mock: MockTaskAdapter, worker_id: str = "c8-it") -> QueueWorker:
    config = WorkerConfig(
        worker_id=worker_id,
        poll_interval_seconds=0.01,
        task_type_codes=["parser_add_channel"],
    )
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


async def _run_worker_until_task_done(worker: QueueWorker, task_id: int) -> None:
    run_task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(
            _wait_until_status(task_id, expected="done", timeout=5.0),
            timeout=5.0,
        )
    finally:
        worker.stop()
        await asyncio.wait_for(run_task, timeout=5.0)


async def _wait_until_status(
    task_id: int, *, expected: str, timeout: float, interval: float = 0.02
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await _task_status(task_id)
        if status == expected:
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"task {task_id} not {expected} within {timeout}s")


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_enqueue_worker_one_iteration_mock_adapter_done(clean_queue) -> None:
    """C8: enqueue → worker loop (1 итерация) → status=done через MockTaskAdapter."""
    account_id, session_name = await _insert_account(session_suffix="primary")
    locked = await _lock_all_free_accounts_except({account_id})
    mock = MockTaskAdapter()
    worker = _build_worker(mock=mock)

    try:
        task_id = await _enqueue(payload=dict(_TEST_PAYLOAD))
        await _run_worker_until_task_done(worker, task_id)

        async with db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT status, account_id, attempt_count, locked_until
                FROM task_queue
                WHERE id = $1
                """,
                task_id,
            )
            current_task = await conn.fetchval(
                "SELECT current_task_id FROM accounts WHERE id = $1", account_id
            )

        assert row["status"] == "done"
        assert row["account_id"] == account_id
        assert current_task is None
        executions = [e for e in mock.executions if e.task_id == task_id]
        assert len(executions) == 1
        execution = executions[0]
        assert execution.task_id == task_id
        assert execution.task_type_code == "parser_add_channel"
        assert execution.session_name == session_name
        assert execution.payload.get("ref") == _TEST_PAYLOAD["ref"]
    finally:
        await _unlock_accounts(locked)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_integration_reserve_atomic_on_done(clean_queue) -> None:
    """C8: после done аккаунт освобождён, attempt_count >= 1, lock снят."""
    account_id, _session_name = await _insert_account(session_suffix="reserve")
    locked = await _lock_all_free_accounts_except({account_id})
    mock = MockTaskAdapter()
    worker = _build_worker(mock=mock, worker_id="c8-reserve-it")

    try:
        task_id = await _enqueue()
        await _run_worker_until_task_done(worker, task_id)

        async with db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT status, attempt_count, locked_until, finished_at
                FROM task_queue
                WHERE id = $1
                """,
                task_id,
            )
            current_task = await conn.fetchval(
                "SELECT current_task_id FROM accounts WHERE id = $1", account_id
            )
            in_progress_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM task_queue
                WHERE id = $1 AND status = 'in_progress'
                """,
                task_id,
            )

        assert row["status"] == "done"
        assert row["status"] != "in_progress"
        assert int(row["attempt_count"]) >= 1
        assert row["locked_until"] is None
        assert row["finished_at"] is not None
        assert current_task is None
        assert in_progress_count == 0
        executions = [e for e in mock.executions if e.task_id == task_id]
        assert len(executions) == 1
    finally:
        await _unlock_accounts(locked)
