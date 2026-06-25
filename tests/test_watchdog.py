"""C6 — watchdog: in_progress → stuck по task_timeout_seconds."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.task_queue import (
    WATCHDOG_STUCK_REASON,
    EnqueueInput,
    StuckTaskResult,
    TaskQueueRepo,
)
from app_balance.queue.watchdog import StuckTaskWatchdog
from app_balance.queue_worker import QueueWorker, WorkerConfig
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_c6_watchdog_"
_CODES = ["parser_add_channel"]
_TEST_PRIO = 2_000_000_000


def _key() -> str:
    return f"{_PREFIX}{uuid.uuid4().hex}"


class FakeQueueForWatchdog:
    def __init__(self, stuck: list[StuckTaskResult] | None = None) -> None:
        self._stuck = list(stuck or [])
        self.calls = 0

    async def mark_stuck_timed_out(
        self, *, limit: int = 100, auto_retry=None
    ) -> list[StuckTaskResult]:
        self.calls += 1
        self.last_auto_retry = auto_retry
        return list(self._stuck)

    async def claim_next(self, locked_by, lock_ttl_seconds, task_type_codes):
        return None


@pytest.mark.asyncio
async def test_watchdog_tick_once_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    stuck = [
        StuckTaskResult(
            id=42,
            task_type_code="parser_add_channel",
            locked_by="worker-1",
            account_id=7,
            source_account_id=None,
            target_account_id=None,
        )
    ]
    queue = FakeQueueForWatchdog(stuck)
    stop = asyncio.Event()
    watchdog = StuckTaskWatchdog(queue, interval_seconds=0.01, stop=stop)

    with caplog.at_level("WARNING", logger="app_balance.queue.watchdog"):
        result = await watchdog.tick_once()

    assert result == stuck
    assert queue.calls == 1
    assert any("id=42" in r.message and "stuck" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_watchdog_run_stops_on_event() -> None:
    queue = FakeQueueForWatchdog()
    stop = asyncio.Event()
    watchdog = StuckTaskWatchdog(queue, interval_seconds=0.01, stop=stop)

    run_task = asyncio.create_task(watchdog.run())
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(run_task, timeout=1.0)
    assert queue.calls >= 1


@pytest.mark.asyncio
async def test_worker_serve_starts_and_stops_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    pool_inited = False
    pool_closed = False
    wd_started = asyncio.Event()

    async def fake_init_pool() -> None:
        nonlocal pool_inited
        pool_inited = True

    async def fake_close_pool() -> None:
        nonlocal pool_closed
        pool_closed = True

    class TrackingWatchdog:
        def __init__(self, queue, *, interval_seconds, stop, auto_retry=None) -> None:
            self._stop = stop

        async def run(self) -> None:
            wd_started.set()
            await self._stop.wait()

    async def fake_run(self) -> None:
        await self._stop.wait()

    monkeypatch.setattr("app_balance.queue_worker.db.init_pool", fake_init_pool)
    monkeypatch.setattr("app_balance.queue_worker.db.close_pool", fake_close_pool)
    monkeypatch.setattr("app_balance.queue_worker.StuckTaskWatchdog", TrackingWatchdog)
    monkeypatch.setattr(QueueWorker, "run", fake_run)
    monkeypatch.setattr(
        "app_balance.queue_worker._install_signal_handlers", lambda _stop: None
    )

    worker = QueueWorker(
        WorkerConfig(
            worker_id="wd-test",
            poll_interval_seconds=0.01,
            watchdog_enabled=True,
            watchdog_interval_seconds=0.01,
        ),
        queue=FakeQueueForWatchdog(),
    )

    serve_task = asyncio.create_task(worker.serve())
    await asyncio.wait_for(wd_started.wait(), timeout=1.0)
    worker.stop()
    await asyncio.wait_for(serve_task, timeout=1.0)

    assert pool_inited
    assert pool_closed
    assert wd_started.is_set()
    assert serve_task.done()


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


async def _enqueue() -> int:
    res = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=_key(),
            priority=_TEST_PRIO,
        )
    )
    assert res.created and res.task_id is not None
    return res.task_id


async def _insert_test_account() -> int:
    session_name = f"{_PREFIX}{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_mark_stuck_timed_out_releases_account(clean_queue) -> None:
    repo = TaskQueueRepo()
    accounts = AccountsRepo()
    task_id = await _enqueue()
    account_id = await _insert_test_account()

    claimed = await repo.claim_next(locked_by="wd-worker", task_type_codes=_CODES)
    assert claimed is not None and claimed.id == task_id

    assert await accounts.reserve(account_id, task_id)

    async with db.acquire() as conn:
        await conn.execute(
            """
            UPDATE task_types
            SET task_timeout_seconds = 5
            WHERE code = 'parser_add_channel'
            """
        )
        await conn.execute(
            """
            UPDATE task_queue
            SET locked_at = now() - interval '10 seconds',
                account_id = $2
            WHERE id = $1
            """,
            task_id,
            account_id,
        )

    stuck = await repo.mark_stuck_timed_out()
    our_stuck = [s for s in stuck if s.id == task_id]
    assert len(our_stuck) == 1

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT status, last_error, locked_by, locked_at, locked_until
            FROM task_queue WHERE id = $1
            """,
            task_id,
        )
        current_task = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", account_id
        )

    assert row["status"] == "stuck"
    assert row["last_error"] == WATCHDOG_STUCK_REASON
    assert row["locked_by"] is None
    assert row["locked_at"] is None
    assert row["locked_until"] is None
    assert current_task is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_watchdog_tick_once_integration(clean_queue, caplog) -> None:
    repo = TaskQueueRepo()
    task_id = await _enqueue()
    claimed = await repo.claim_next(locked_by="wd-it", task_type_codes=_CODES)
    assert claimed is not None

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_types SET task_timeout_seconds = 1 "
            "WHERE code = 'parser_add_channel'"
        )
        await conn.execute(
            "UPDATE task_queue SET locked_at = now() - interval '5 seconds' "
            "WHERE id = $1",
            task_id,
        )

    stop = asyncio.Event()
    watchdog = StuckTaskWatchdog(repo, interval_seconds=30.0, stop=stop)

    with caplog.at_level("WARNING", logger="app_balance.queue.watchdog"):
        await watchdog.tick_once()

    assert any("→ stuck" in r.message for r in caplog.records)

    async with db.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", task_id
        )
    assert status == "stuck"


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_does_not_overwrite_stuck(clean_queue) -> None:
    repo = TaskQueueRepo()
    task_id = await _enqueue()
    await repo.claim_next(locked_by="wd-guard", task_type_codes=_CODES)

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_types SET task_timeout_seconds = 1 "
            "WHERE code = 'parser_add_channel'"
        )
        await conn.execute(
            "UPDATE task_queue SET locked_at = now() - interval '5 seconds' "
            "WHERE id = $1",
            task_id,
        )

    await repo.mark_stuck_timed_out()
    assert await repo.complete(task_id) is False

    async with db.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", task_id
        )
    assert status == "stuck"
