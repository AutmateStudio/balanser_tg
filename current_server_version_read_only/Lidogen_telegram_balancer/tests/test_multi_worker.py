"""C9 — multi-worker integration test: 2 OS-процесса, SKIP LOCKED, без дублей task_id."""
from __future__ import annotations

import asyncio
import multiprocessing
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

_PREFIX = "test_c9_multi_"
_HOLDER_PRIORITY = -2_000_000_000
_TEST_PRIORITY = 2_000_000_000
_TEST_PAYLOAD = {"ref": "@c9_test"}
_TASK_COUNT = 8
_WORKER_IDS = ("c9-w1", "c9-w2")
_DONE_TIMEOUT_SECONDS = 30.0
_JOIN_TIMEOUT_SECONDS = 10.0


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


async def _enqueue(*, account_id: int) -> int:
    res = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=f"{_PREFIX}{uuid.uuid4().hex}",
            priority=_TEST_PRIORITY,
            account_id=account_id,
            payload=dict(_TEST_PAYLOAD),
        )
    )
    assert res.created and res.task_id is not None
    return res.task_id


async def _insert_account(*, session_suffix: str) -> int:
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
    return int(account_id)


async def _occupy_account(account_id: int) -> int:
    res = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=f"{_PREFIX}holder_{uuid.uuid4().hex}",
            priority=_HOLDER_PRIORITY,
        )
    )
    assert res.created and res.task_id is not None
    holder_task_id = res.task_id
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


def _build_worker(*, worker_id: str) -> QueueWorker:
    config = WorkerConfig(
        worker_id=worker_id,
        poll_interval_seconds=0.01,
        task_type_codes=["parser_add_channel"],
        watchdog_enabled=False,
    )
    dispatcher = TaskDispatcher(
        queue=TaskQueueRepo(),
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=MockTaskAdapter(),
        resource_check=ResourceChecker(ResourceUsageRepo()),
        postpone_delay_seconds=config.postpone_delay_seconds,
        retry_delay_seconds=config.retry_delay_seconds,
    )
    return QueueWorker(config, dispatcher=dispatcher)


async def _worker_async_main(
    worker_id: str,
    stop_event: multiprocessing.synchronize.Event,
    processed_by_worker: dict[str, int] | None = None,
) -> None:
    await db.init_pool()
    worker = _build_worker(worker_id=worker_id)
    run_task = asyncio.create_task(worker.run())
    stats_task = asyncio.create_task(
        _track_processed(worker, worker_id, processed_by_worker, stop_event)
    )
    stop_task = asyncio.create_task(asyncio.to_thread(stop_event.wait))
    try:
        done, pending = await asyncio.wait(
            [run_task, stop_task], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        worker.stop()
        if not run_task.done():
            await asyncio.wait_for(run_task, timeout=_JOIN_TIMEOUT_SECONDS)
        for task in done:
            if task is run_task and task.exception() is not None:
                raise task.exception()
    finally:
        stats_task.cancel()
        try:
            await stats_task
        except asyncio.CancelledError:
            pass
        if processed_by_worker is not None:
            processed_by_worker[worker_id] = worker.processed
        await db.close_pool()


async def _track_processed(
    worker: QueueWorker,
    worker_id: str,
    processed_by_worker: dict[str, int] | None,
    stop_event: multiprocessing.synchronize.Event,
) -> None:
    last = 0
    while not stop_event.is_set():
        current = worker.processed
        if processed_by_worker is not None and current > last:
            processed_by_worker[worker_id] = current
            last = current
        await asyncio.sleep(0.01)


def _worker_process_entry(
    worker_id: str,
    stop_event: multiprocessing.synchronize.Event,
    processed_by_worker: dict[str, int] | None = None,
) -> None:
    asyncio.run(_worker_async_main(worker_id, stop_event, processed_by_worker))


async def _wait_all_done(
    task_ids: list[int], *, timeout: float
) -> set[str]:
    """Ждём N done; параллельно собираем locked_by, пока задачи in_progress."""
    seen_workers: set[str] = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with db.acquire() as conn:
            done_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM task_queue
                WHERE id = ANY($1::bigint[]) AND status = 'done'
                """,
                task_ids,
            )
            worker_rows = await conn.fetch(
                """
                SELECT DISTINCT locked_by
                FROM task_queue
                WHERE id = ANY($1::bigint[])
                  AND locked_by IS NOT NULL
                """,
                task_ids,
            )
        for row in worker_rows:
            worker_id = row["locked_by"]
            if worker_id in _WORKER_IDS:
                seen_workers.add(worker_id)
        if int(done_count) == len(task_ids):
            return seen_workers
        await asyncio.sleep(0.05)
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, status, locked_by
            FROM task_queue
            WHERE id = ANY($1::bigint[])
            ORDER BY id
            """,
            task_ids,
        )
    snapshot = [(int(r["id"]), r["status"], r["locked_by"]) for r in rows]
    raise TimeoutError(
        f"not all tasks done within {timeout}s; snapshot={snapshot}"
    )


def _stop_processes(
    processes: list[multiprocessing.Process],
    stop_event: multiprocessing.synchronize.Event,
) -> None:
    stop_event.set()
    for proc in processes:
        proc.join(timeout=_JOIN_TIMEOUT_SECONDS)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5.0)
        assert not proc.is_alive(), f"worker pid={proc.pid} did not stop"
        assert proc.exitcode == 0, f"worker pid={proc.pid} exitcode={proc.exitcode}"


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_workers_process_n_tasks_without_duplicates(clean_queue) -> None:
    """C9: N задач, 2 OS-процесса → ровно N done, без дублей task_id."""
    account_ids = [
        await _insert_account(session_suffix=f"acc{i}") for i in range(_TASK_COUNT)
    ]
    locked = await _lock_all_free_accounts_except(set(account_ids))

    ctx = multiprocessing.get_context("spawn")
    stop_event = ctx.Event()
    manager = ctx.Manager()
    processed_by_worker = manager.dict()
    processes = [
        ctx.Process(
            target=_worker_process_entry,
            args=(worker_id, stop_event, processed_by_worker),
            name=worker_id,
        )
        for worker_id in _WORKER_IDS
    ]

    try:
        for proc in processes:
            proc.start()

        task_ids = [
            await _enqueue(account_id=account_id)
            for account_id in account_ids
        ]

        seen_workers = await _wait_all_done(task_ids, timeout=_DONE_TIMEOUT_SECONDS)
        _stop_processes(processes, stop_event)

        active_workers = {
            worker_id
            for worker_id in _WORKER_IDS
            if int(processed_by_worker.get(worker_id, 0)) >= 1
        }
        active_workers |= seen_workers

        async with db.acquire() as conn:
            done_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM task_queue
                WHERE id = ANY($1::bigint[]) AND status = 'done'
                """,
                task_ids,
            )
            in_progress_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM task_queue
                WHERE id = ANY($1::bigint[]) AND status = 'in_progress'
                """,
                task_ids,
            )
            distinct_done = await conn.fetchval(
                """
                SELECT COUNT(DISTINCT id) FROM task_queue
                WHERE id = ANY($1::bigint[]) AND status = 'done'
                """,
                task_ids,
            )
            rows = await conn.fetch(
                """
                SELECT id, status, attempt_count, locked_by, locked_until, finished_at
                FROM task_queue
                WHERE id = ANY($1::bigint[])
                ORDER BY id
                """,
                task_ids,
            )
            busy_accounts = await conn.fetchval(
                """
                SELECT COUNT(*) FROM accounts
                WHERE id = ANY($1::bigint[]) AND current_task_id IS NOT NULL
                """,
                account_ids,
            )

        assert int(done_count) == _TASK_COUNT
        assert int(in_progress_count) == 0
        assert int(distinct_done) == _TASK_COUNT
        assert len(active_workers) >= 2, (
            f"expected both workers to process tasks, "
            f"locked_by={sorted(seen_workers)}, processed={dict(processed_by_worker)}"
        )
        assert int(busy_accounts) == 0

        for row in rows:
            assert row["status"] == "done"
            assert int(row["attempt_count"]) >= 1
            assert row["locked_by"] is None
            assert row["locked_until"] is None
            assert row["finished_at"] is not None
    finally:
        stop_event.set()
        for proc in processes:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5.0)
        manager.shutdown()
        await _unlock_accounts(locked)
