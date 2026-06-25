"""G5 — watchdog auto-retry зависших задач (ТЗ §13.4, §30.19).

Unit: WatchdogAutoRetryConfig.from_env + tick_once логи по outcome.
Integration (shared PG): mark_stuck_timed_out в режимах stuck / retry / failed.
"""
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
from app_balance.queue.watchdog import StuckTaskWatchdog, WatchdogAutoRetryConfig
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_g5_"
_TASK_TYPE = "parser_add_channel"
_CODES = [_TASK_TYPE]
_TEST_PRIO = 2_000_000_000


def _key() -> str:
    return f"{_PREFIX}{uuid.uuid4().hex}"


# --------------------------------------------------------------------------- #
# Unit                                                                        #
# --------------------------------------------------------------------------- #


def test_auto_retry_config_defaults() -> None:
    cfg = WatchdogAutoRetryConfig()
    assert cfg.enabled is False
    assert cfg.max_attempts == 2
    assert cfg.delay_seconds == 60


def test_auto_retry_config_from_env_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WATCHDOG_AUTO_RETRY_ENABLED", raising=False)
    cfg = WatchdogAutoRetryConfig.from_env()
    assert cfg.enabled is False


def test_auto_retry_config_from_env_parses_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WATCHDOG_AUTO_RETRY_ENABLED", "true")
    monkeypatch.setenv("WATCHDOG_AUTO_RETRY_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("WATCHDOG_AUTO_RETRY_DELAY_SECONDS", "120")
    cfg = WatchdogAutoRetryConfig.from_env()
    assert cfg.enabled is True
    assert cfg.max_attempts == 5
    assert cfg.delay_seconds == 120


class _FakeQueue:
    def __init__(self, results: list[StuckTaskResult]) -> None:
        self._results = results
        self.calls = 0
        self.last_auto_retry: WatchdogAutoRetryConfig | None = None

    async def mark_stuck_timed_out(
        self, *, limit: int = 100, auto_retry=None
    ) -> list[StuckTaskResult]:
        self.calls += 1
        self.last_auto_retry = auto_retry
        return list(self._results)


@pytest.mark.asyncio
async def test_tick_passes_auto_retry_config() -> None:
    queue = _FakeQueue([])
    cfg = WatchdogAutoRetryConfig(enabled=True, max_attempts=3, delay_seconds=30)
    watchdog = StuckTaskWatchdog(
        queue, interval_seconds=0.01, stop=asyncio.Event(), auto_retry=cfg
    )
    await watchdog.tick_once()
    assert queue.last_auto_retry is cfg


@pytest.mark.asyncio
async def test_tick_logs_retry_outcome(caplog: pytest.LogCaptureFixture) -> None:
    results = [
        StuckTaskResult(
            id=11,
            task_type_code=_TASK_TYPE,
            locked_by="worker-1",
            account_id=3,
            source_account_id=None,
            target_account_id=None,
            outcome="retry",
            attempt_count=0,
            max_attempts=3,
            watchdog_retry_count=1,
        )
    ]
    queue = _FakeQueue(results)
    watchdog = StuckTaskWatchdog(
        queue,
        interval_seconds=0.01,
        stop=asyncio.Event(),
        auto_retry=WatchdogAutoRetryConfig(enabled=True),
    )
    with caplog.at_level("INFO", logger="app_balance.queue.watchdog"):
        await watchdog.tick_once()
    assert any("auto-retry id=11" in r.message and "retry" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_tick_logs_failed_outcome(caplog: pytest.LogCaptureFixture) -> None:
    results = [
        StuckTaskResult(
            id=12,
            task_type_code=_TASK_TYPE,
            locked_by="worker-1",
            account_id=3,
            source_account_id=None,
            target_account_id=None,
            outcome="failed",
            attempt_count=3,
            max_attempts=3,
            watchdog_retry_count=2,
        )
    ]
    queue = _FakeQueue(results)
    watchdog = StuckTaskWatchdog(
        queue,
        interval_seconds=0.01,
        stop=asyncio.Event(),
        auto_retry=WatchdogAutoRetryConfig(enabled=True),
    )
    with caplog.at_level("WARNING", logger="app_balance.queue.watchdog"):
        await watchdog.tick_once()
    assert any("исчерпан id=12" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# Integration (shared PG)                                                     #
# --------------------------------------------------------------------------- #


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
            task_type_code=_TASK_TYPE,
            dedup_key=_key(),
            priority=_TEST_PRIO,
        )
    )
    assert res.created and res.task_id is not None
    return int(res.task_id)


async def _insert_account() -> int:
    session_name = f"{_PREFIX}{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        return int(
            await conn.fetchval(
                "INSERT INTO accounts (session_name, status, is_enabled) "
                "VALUES ($1, 'active', true) RETURNING id",
                session_name,
            )
        )


async def _force_timed_out(task_id: int, account_id: int) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_types SET task_timeout_seconds = 1 WHERE code = $1",
            _TASK_TYPE,
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
        await conn.execute(
            "UPDATE accounts SET current_task_id = $2 WHERE id = $1",
            account_id,
            task_id,
        )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_retry_disabled_marks_stuck(clean_queue) -> None:
    """enabled=False (default) — поведение C6: timed-out → stuck."""
    repo = TaskQueueRepo()
    task_id = await _enqueue()
    account_id = await _insert_account()
    claimed = await repo.claim_next(locked_by="g5-stuck", task_type_codes=_CODES)
    assert claimed is not None and claimed.id == task_id
    await _force_timed_out(task_id, account_id)

    results = await repo.mark_stuck_timed_out(
        auto_retry=WatchdogAutoRetryConfig(enabled=False)
    )
    ours = [r for r in results if r.id == task_id]
    assert len(ours) == 1 and ours[0].outcome == "stuck"

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, last_error FROM task_queue WHERE id = $1", task_id
        )
        current = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", account_id
        )
    assert row["status"] == "stuck"
    assert row["last_error"] == WATCHDOG_STUCK_REASON
    assert current is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_retry_enabled_reschedules(clean_queue) -> None:
    """enabled=True, остались попытки → retry, run_after в будущем, счётчик +1."""
    repo = TaskQueueRepo()
    task_id = await _enqueue()
    account_id = await _insert_account()
    claimed = await repo.claim_next(locked_by="g5-retry", task_type_codes=_CODES)
    assert claimed is not None and claimed.id == task_id
    await _force_timed_out(task_id, account_id)

    cfg = WatchdogAutoRetryConfig(enabled=True, max_attempts=2, delay_seconds=60)
    results = await repo.mark_stuck_timed_out(auto_retry=cfg)
    ours = [r for r in results if r.id == task_id]
    assert len(ours) == 1
    assert ours[0].outcome == "retry"
    assert ours[0].watchdog_retry_count == 1

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT status, run_after, last_error,
                   (payload->>'watchdog_retry_count')::int AS wd
            FROM task_queue WHERE id = $1
            """,
            task_id,
        )
        current = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", account_id
        )
    assert row["status"] == "retry"
    assert row["run_after"] is not None
    assert row["wd"] == 1
    assert row["last_error"] == WATCHDOG_STUCK_REASON
    assert current is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_retry_reclaimable_after_delay(clean_queue) -> None:
    """После auto-retry задача снова claimable (run_after в прошлом)."""
    repo = TaskQueueRepo()
    task_id = await _enqueue()
    account_id = await _insert_account()
    claimed = await repo.claim_next(locked_by="g5-reclaim", task_type_codes=_CODES)
    assert claimed is not None
    await _force_timed_out(task_id, account_id)

    await repo.mark_stuck_timed_out(
        auto_retry=WatchdogAutoRetryConfig(enabled=True, max_attempts=2, delay_seconds=60)
    )
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET run_after = now() - interval '1 second' WHERE id = $1",
            task_id,
        )
    reclaimed = await repo.claim_by_id(task_id, locked_by="g5-reclaim-2")
    assert reclaimed is not None and reclaimed.id == task_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_retry_exhausted_attempts_fails(clean_queue) -> None:
    """attempt_count >= max_attempts → failed несмотря на enabled."""
    repo = TaskQueueRepo()
    task_id = await _enqueue()
    account_id = await _insert_account()
    claimed = await repo.claim_next(locked_by="g5-fail", task_type_codes=_CODES)
    assert claimed is not None
    await _force_timed_out(task_id, account_id)
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET attempt_count = max_attempts WHERE id = $1",
            task_id,
        )

    results = await repo.mark_stuck_timed_out(
        auto_retry=WatchdogAutoRetryConfig(enabled=True, max_attempts=2, delay_seconds=60)
    )
    ours = [r for r in results if r.id == task_id]
    assert len(ours) == 1 and ours[0].outcome == "failed"

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, finished_at FROM task_queue WHERE id = $1", task_id
        )
    assert row["status"] == "failed"
    assert row["finished_at"] is not None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_retry_cap_exhausted_fails(clean_queue) -> None:
    """watchdog_retry_count >= cap → failed (защита от бесконечного цикла)."""
    repo = TaskQueueRepo()
    task_id = await _enqueue()
    account_id = await _insert_account()
    claimed = await repo.claim_next(locked_by="g5-cap", task_type_codes=_CODES)
    assert claimed is not None
    await _force_timed_out(task_id, account_id)
    async with db.acquire() as conn:
        await conn.execute(
            """
            UPDATE task_queue
            SET payload = jsonb_set(
                COALESCE(payload, '{}'::jsonb),
                '{watchdog_retry_count}', to_jsonb(2), true)
            WHERE id = $1
            """,
            task_id,
        )

    results = await repo.mark_stuck_timed_out(
        auto_retry=WatchdogAutoRetryConfig(enabled=True, max_attempts=2, delay_seconds=60)
    )
    ours = [r for r in results if r.id == task_id]
    assert len(ours) == 1 and ours[0].outcome == "failed"
