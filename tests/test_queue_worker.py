"""C1 — тесты воркера очереди.

Юнит-часть (без БД, на фейковых репозиториях) проверяет логику цикла и
graceful-shutdown. Интеграционная часть проверяет finalize-методы на живой PG.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.dispatch import DispatchResult
from app_balance.queue.task_queue import ClaimedTask, EnqueueInput, TaskQueueRepo
from app_balance.queue_worker import QueueWorker, WorkerConfig
from tests.conftest import requires_pg, TEST_ISOLATION_PRIORITY
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_c1_"


def make_claimed(task_id: int, account_id: int | None) -> ClaimedTask:
    return ClaimedTask(
        id=task_id,
        task_type_id=1,
        task_type_code="parser_add_channel",
        priority=0,
        payload={},
        channel_id=None,
        account_id=account_id,
        source_account_id=None,
        target_account_id=None,
        attempt_count=1,
        max_attempts=3,
        dedup_key=None,
        locked_by="test",
        locked_until=None,
    )


# --------------------------------------------------------------------------- #
# Фейки для юнит-тестов цикла
# --------------------------------------------------------------------------- #
class FakeQueue:
    def __init__(self, tasks: list) -> None:
        self._tasks = list(tasks)
        self.completed: list[int] = []
        self.failed: list[tuple[int, str | None]] = []

    async def claim_next(self, locked_by, lock_ttl_seconds, task_type_codes):
        return self._tasks.pop(0) if self._tasks else None

    async def complete(self, task_id):
        self.completed.append(task_id)
        return True

    async def reschedule_or_fail(self, task_id, error, retry_delay_seconds):
        self.failed.append((task_id, error))
        return "retry"


class FakeAccounts:
    def __init__(self) -> None:
        self.released: list[int] = []

    async def release(self, account_id):
        self.released.append(account_id)


class FakeDispatcher:
    """C3: имитация dispatch — postpone, затем complete."""

    def __init__(self, results: list[DispatchResult]) -> None:
        self._results = list(results)
        self.calls: list[ClaimedTask] = []

    async def dispatch(self, task: ClaimedTask) -> DispatchResult:
        self.calls.append(task)
        if not self._results:
            return DispatchResult.COMPLETED
        return self._results.pop(0)


def _cfg() -> WorkerConfig:
    return WorkerConfig(worker_id="test", poll_interval_seconds=0.01)


@pytest.mark.asyncio
async def test_run_exits_on_stop() -> None:
    worker = QueueWorker(_cfg(), queue=FakeQueue([]), accounts=FakeAccounts())
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.03)
    worker.stop()
    await asyncio.wait_for(task, timeout=1.0)
    assert task.done()


@pytest.mark.asyncio
async def test_processes_task_and_completes() -> None:
    claimed = make_claimed(task_id=10, account_id=None)
    queue = FakeQueue([claimed])
    accounts = FakeAccounts()
    handled: list[int] = []

    async def handler(t):
        handled.append(t.id)

    worker = QueueWorker(_cfg(), queue=queue, accounts=accounts, handler=handler)
    run = asyncio.create_task(worker.run())
    while worker.processed < 1:
        await asyncio.sleep(0.005)
    worker.stop()
    await asyncio.wait_for(run, timeout=1.0)

    assert handled == [10]
    assert queue.completed == [10]
    assert worker.processed == 1


@pytest.mark.asyncio
async def test_releases_account_after_processing() -> None:
    claimed = make_claimed(task_id=11, account_id=555)
    queue = FakeQueue([claimed])
    accounts = FakeAccounts()

    async def noop(_t):
        pass

    worker = QueueWorker(_cfg(), queue=queue, accounts=accounts, handler=noop)
    run = asyncio.create_task(worker.run())
    while worker.processed < 1:
        await asyncio.sleep(0.005)
    worker.stop()
    await asyncio.wait_for(run, timeout=1.0)

    assert accounts.released == [555]


@pytest.mark.asyncio
async def test_handler_error_reschedules_and_releases() -> None:
    claimed = make_claimed(task_id=12, account_id=777)
    queue = FakeQueue([claimed])
    accounts = FakeAccounts()

    async def boom(_t):
        raise RuntimeError(" boom ")

    worker = QueueWorker(_cfg(), queue=queue, accounts=accounts, handler=boom)
    run = asyncio.create_task(worker.run())
    while not queue.failed:
        await asyncio.sleep(0.005)
    worker.stop()
    await asyncio.wait_for(run, timeout=1.0)

    assert queue.failed and queue.failed[0][0] == 12
    assert queue.completed == []
    assert accounts.released == [777]
    assert worker.processed == 0


@pytest.mark.asyncio
async def test_worker_continues_after_postponed_task() -> None:
    """C3: после POSTPONED воркер берёт следующую задачу и завершает её."""
    queue = FakeQueue(
        [
            make_claimed(task_id=20, account_id=None),
            make_claimed(task_id=21, account_id=None),
        ]
    )
    dispatcher = FakeDispatcher(
        [DispatchResult.POSTPONED, DispatchResult.COMPLETED]
    )
    worker = QueueWorker(
        _cfg(),
        queue=queue,
        accounts=FakeAccounts(),
        dispatcher=dispatcher,
    )

    run = asyncio.create_task(worker.run())
    while worker.processed < 1:
        await asyncio.sleep(0.005)
    worker.stop()
    await asyncio.wait_for(run, timeout=1.0)

    assert [t.id for t in dispatcher.calls] == [20, 21]
    assert len(dispatcher.calls) == 2
    assert worker.processed == 1
    assert queue.completed == []


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_ID", "w-7")
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("WORKER_LOCK_TTL_SECONDS", "120")
    monkeypatch.setenv("WORKER_POSTPONE_DELAY_SECONDS", "600")
    monkeypatch.setenv("WORKER_TASK_TYPES", "parser_add_channel, move_channel")
    monkeypatch.setenv("WORKER_WATCHDOG_ENABLED", "false")
    monkeypatch.setenv("WORKER_WATCHDOG_INTERVAL_SECONDS", "45")
    cfg = WorkerConfig.from_env()
    assert cfg.worker_id == "w-7"
    assert cfg.poll_interval_seconds == 2.5
    assert cfg.lock_ttl_seconds == 120
    assert cfg.postpone_delay_seconds == 600
    assert cfg.task_type_codes == ["parser_add_channel", "move_channel"]
    assert cfg.watchdog_enabled is False
    assert cfg.watchdog_interval_seconds == 45.0


def test_build_task_adapter_mock_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKER_TASK_ADAPTER", raising=False)
    from app_balance.queue.mock_adapter import MockTaskAdapter
    from app_balance.queue_worker import build_task_adapter

    assert isinstance(build_task_adapter(), MockTaskAdapter)


def test_build_task_adapter_clump_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from app_balance.queue.adapter import ClumpTaskAdapter
    from app_balance.queue_worker import build_task_adapter

    for mode in ("clump", "telethon", "real"):
        monkeypatch.setenv("WORKER_TASK_ADAPTER", mode)
        assert isinstance(build_task_adapter(), ClumpTaskAdapter)


def test_build_default_dispatcher_wires_resource_check() -> None:
    from app_balance.queue.dispatch import TaskDispatcher
    from app_balance.queue.resource_check import ResourceChecker
    from app_balance.queue_worker import build_default_dispatcher

    dispatcher = build_default_dispatcher(_cfg())
    assert isinstance(dispatcher, TaskDispatcher)
    assert isinstance(dispatcher._resource_check, ResourceChecker)


# --------------------------------------------------------------------------- #
# Интеграция: finalize-методы и сквозной прогон воркера
# --------------------------------------------------------------------------- #
@pytest.fixture
async def clean_queue(pg_pool):
    async def _cleanup() -> None:
        await cleanup_queue_test_data(dedup_key_like=f"{_PREFIX}%")

    await _cleanup()
    yield
    await _cleanup()


async def _enqueue(priority: int | None = TEST_ISOLATION_PRIORITY) -> int:
    res = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=f"{_PREFIX}{uuid.uuid4().hex}",
            priority=priority,
        )
    )
    return res.task_id


async def _cleanup_test_account(session_name: str) -> None:
    """Снимает FK (usage, task_queue) перед DELETE тестового аккаунта."""
    await cleanup_queue_test_data(session_name_eq=session_name)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_marks_done(clean_queue) -> None:
    repo = TaskQueueRepo()
    task_id = await _enqueue()
    await repo.claim_next(locked_by="w")
    await repo.complete(task_id)

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, finished_at, locked_until FROM task_queue WHERE id = $1",
            task_id,
        )
    assert row["status"] == "done"
    assert row["finished_at"] is not None
    assert row["locked_until"] is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_reschedule_retry_when_attempts_remain(clean_queue) -> None:
    repo = TaskQueueRepo()
    task_id = await _enqueue(priority=TEST_ISOLATION_PRIORITY)
    claimed = await repo.claim_next(locked_by="w")
    assert claimed.id == task_id
    assert claimed.max_attempts >= 2  # parser_add_channel допускает ретраи

    status = await repo.reschedule_or_fail(task_id, "temporary", retry_delay_seconds=30)
    assert status == "retry"

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, last_error, run_after, locked_until FROM task_queue WHERE id = $1",
            task_id,
        )
    assert row["status"] == "retry"
    assert row["last_error"] == "temporary"
    assert row["locked_until"] is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_reschedule_fails_when_attempts_exhausted(clean_queue) -> None:
    repo = TaskQueueRepo()
    task_id = await _enqueue()
    await repo.claim_next(locked_by="w")

    # Симулируем исчерпание попыток.
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET attempt_count = max_attempts WHERE id = $1", task_id
        )

    status = await repo.reschedule_or_fail(task_id, "fatal")
    assert status == "failed"

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, finished_at FROM task_queue WHERE id = $1", task_id
        )
    assert row["status"] == "failed"
    assert row["finished_at"] is not None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_processes_one_task_end_to_end(clean_queue) -> None:
    """B3 → B4 → C1: воркер захватывает и завершает задачу, затем останавливается."""
    task_id = await _enqueue(priority=TEST_ISOLATION_PRIORITY)

    processed_ids: list[int] = []
    worker = QueueWorker(
        WorkerConfig(
            worker_id="it-worker",
            poll_interval_seconds=0.01,
            task_type_codes=["parser_add_channel"],
        ),
    )

    async def handler(t):
        processed_ids.append(t.id)
        worker.stop()  # берём ровно одну задачу

    worker._legacy_handler = handler  # noqa: SLF001 — legacy-путь C1
    worker._dispatcher = None

    await asyncio.wait_for(worker.run(), timeout=5.0)

    assert task_id in processed_ids
    async with db.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", task_id
        )
    assert status == "done"


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_dispatch_with_account_released(clean_queue, pg_pool) -> None:
    """C2: enqueue → worker (dispatch) → done, current_task_id сброшен."""
    session_name = f"{_PREFIX}acc_{uuid.uuid4().hex}"

    await _cleanup_test_account(session_name)

    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )

    try:
        task_id = await _enqueue(priority=TEST_ISOLATION_PRIORITY)

        worker = QueueWorker(
            WorkerConfig(
                worker_id="c2-it-worker",
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
            status = await conn.fetchval(
                "SELECT status FROM task_queue WHERE id = $1", task_id
            )
            current_task = await conn.fetchval(
                "SELECT current_task_id FROM accounts WHERE id = $1", account_id
            )
            assigned = await conn.fetchval(
                "SELECT account_id FROM task_queue WHERE id = $1", task_id
            )

        assert status == "done"
        assert current_task is None
        assert assigned == account_id
    finally:
        await _cleanup_test_account(session_name)


async def _wait_until(predicate, *, timeout: float, interval: float = 0.02) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("condition not met")
