"""B5 — интеграционные тесты postpone (in_progress → scheduled)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app_balance.queue import db
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg

_PREFIX = "test_b5_postpone_"
_TEST_PRIO = 2_000_000_000
_CODES = ["parser_add_channel"]


def _key() -> str:
    return f"{_PREFIX}{uuid.uuid4().hex}"


@pytest.fixture
async def clean_queue(pg_pool):
    async def _cleanup() -> None:
        async with db.acquire() as conn:
            await conn.execute(
                "DELETE FROM account_resource_usage WHERE task_id IN "
                "(SELECT id FROM task_queue WHERE dedup_key LIKE $1)",
                f"{_PREFIX}%",
            )
            await conn.execute(
                "DELETE FROM task_queue WHERE dedup_key LIKE $1", f"{_PREFIX}%"
            )

    await _cleanup()
    yield
    await _cleanup()


async def _enqueue(
    priority: int | None = _TEST_PRIO,
    task_type_code: str = "parser_add_channel",
) -> int:
    res = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code=task_type_code,
            dedup_key=_key(),
            priority=priority,
        )
    )
    return res.task_id


async def _fetch_row(task_id: int):
    async with db.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT status, postpone_count, attempt_count,
                   locked_by, locked_at, locked_until,
                   last_error, last_error_at, run_after
            FROM task_queue WHERE id = $1
            """,
            task_id,
        )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_postpone_from_in_progress(clean_queue) -> None:
    task_id = await _enqueue()
    repo = TaskQueueRepo()

    claimed = await repo.claim_next(locked_by="worker-1", task_type_codes=_CODES)
    assert claimed is not None
    assert claimed.id == task_id
    attempt_after_claim = claimed.attempt_count

    await repo.postpone(task_id, delay_seconds=300)

    row = await _fetch_row(task_id)
    assert row["status"] == "scheduled"
    assert row["postpone_count"] == 1
    assert row["attempt_count"] == attempt_after_claim
    assert row["locked_by"] is None
    assert row["locked_at"] is None
    assert row["locked_until"] is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_postpone_writes_reason(clean_queue) -> None:
    task_id = await _enqueue()
    repo = TaskQueueRepo()

    await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    reason = "нет свободного аккаунта"
    await repo.postpone(task_id, delay_seconds=60, reason=reason)

    row = await _fetch_row(task_id)
    assert row["last_error"] == reason
    assert row["last_error_at"] is not None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_postpone_not_claimable_before_run_after(clean_queue) -> None:
    task_id = await _enqueue()
    repo = TaskQueueRepo()

    await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    await repo.postpone(task_id, delay_seconds=3600)

    reclaimed = await repo.claim_next(locked_by="w2", task_type_codes=_CODES)
    assert reclaimed is None or reclaimed.id != task_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_postpone_reclaimable_after_run_after(clean_queue) -> None:
    task_id = await _enqueue()
    repo = TaskQueueRepo()

    await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    await repo.postpone(task_id, delay_seconds=3600)

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET run_after = now() - interval '1 second' "
            "WHERE id = $1",
            task_id,
        )

    reclaimed = await repo.claim_next(locked_by="w2", task_type_codes=_CODES)
    assert reclaimed is not None
    assert reclaimed.id == task_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_double_postpone_increments_count(clean_queue) -> None:
    task_id = await _enqueue()
    repo = TaskQueueRepo()

    claimed = await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    assert claimed is not None
    attempt_after_first_claim = claimed.attempt_count

    await repo.postpone(task_id, delay_seconds=0)

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET run_after = now() - interval '1 second' "
            "WHERE id = $1",
            task_id,
        )

    reclaimed = await repo.claim_next(locked_by="w2", task_type_codes=_CODES)
    assert reclaimed is not None
    assert reclaimed.attempt_count == attempt_after_first_claim

    await repo.postpone(task_id, delay_seconds=0)

    row = await _fetch_row(task_id)
    assert row["postpone_count"] == 2
    assert row["attempt_count"] == attempt_after_first_claim


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_postpone_does_not_write_resource_usage(clean_queue) -> None:
    task_id = await _enqueue()
    repo = TaskQueueRepo()

    await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    await repo.postpone(task_id, delay_seconds=300)

    async with db.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM account_resource_usage WHERE task_id = $1",
            task_id,
        )
    assert count == 0


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_postpone_run_after_in_future(clean_queue) -> None:
    task_id = await _enqueue()
    repo = TaskQueueRepo()
    before = datetime.now(timezone.utc)

    await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    await repo.postpone(task_id, delay_seconds=300)

    row = await _fetch_row(task_id)
    run_after = row["run_after"]
    assert run_after is not None
    assert run_after >= before + timedelta(seconds=290)
