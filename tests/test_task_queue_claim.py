"""B4/C7 — интеграционные тесты claim_next (max priority + random, SKIP LOCKED)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app_balance.queue import db
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg

_PREFIX = "test_b4_"
# Выше PYTEST_TEST_PRIORITY (2_000_000_000): claim_next берёт глобальный MAX(priority),
# на shared PG чужие тестовые задачи на 2B конкурируют с B4.
_TEST_PRIO = 3_000_000_000
_CODES = ["parser_add_channel"]


def _key() -> str:
    return f"{_PREFIX}{uuid.uuid4().hex}"


@pytest.fixture
async def clean_queue(pg_pool):
    async def _cleanup() -> None:
        async with db.acquire() as conn:
            await conn.execute(
                "DELETE FROM task_queue WHERE dedup_key LIKE $1", f"{_PREFIX}%"
            )

    await _cleanup()
    yield
    await _cleanup()


async def _enqueue(
    priority: int | None = _TEST_PRIO,
    run_after: datetime | None = None,
    task_type_code: str = "parser_add_channel",
) -> int:
    res = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code=task_type_code,
            dedup_key=_key(),
            priority=priority,
            run_after=run_after,
        )
    )
    return res.task_id


async def _reset_to_queued(*task_ids: int) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            """
            UPDATE task_queue
            SET status = 'queued',
                locked_by = NULL,
                locked_at = NULL,
                locked_until = NULL,
                attempt_count = 0,
                started_at = NULL,
                finished_at = NULL
            WHERE id = ANY($1::bigint[])
            """,
            list(task_ids),
        )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_marks_in_progress(clean_queue) -> None:
    task_id = await _enqueue()
    repo = TaskQueueRepo()

    claimed = await repo.claim_next(locked_by="worker-1", task_type_codes=_CODES)
    assert claimed is not None
    assert claimed.id == task_id

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, locked_by, attempt_count, started_at, locked_until "
            "FROM task_queue WHERE id = $1",
            claimed.id,
        )
    assert row["status"] == "in_progress"
    assert row["locked_by"] == "worker-1"
    assert row["attempt_count"] == 0
    assert row["started_at"] is not None
    assert row["locked_until"] is not None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_prefers_higher_priority(clean_queue) -> None:
    """Сначала max priority, затем более низкий."""
    low = await _enqueue(priority=_TEST_PRIO - 100)
    high = await _enqueue(priority=_TEST_PRIO)
    repo = TaskQueueRepo()

    first = await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    second = await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    assert first is not None and second is not None
    assert first.id == high
    assert second.id == low


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_three_tiers_only_max_level_first(clean_queue) -> None:
    """Средний priority не берётся, пока есть задачи с более высоким."""
    low = await _enqueue(priority=_TEST_PRIO - 200)
    mid = await _enqueue(priority=_TEST_PRIO - 100)
    high = await _enqueue(priority=_TEST_PRIO)
    repo = TaskQueueRepo()

    first = await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    assert first is not None and first.id == high

    await repo.complete(high)
    second = await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    assert second is not None and second.id == mid

    await repo.complete(mid)
    third = await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    assert third is not None and third.id == low


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_random_among_equal_max_priority(clean_queue) -> None:
    """При равном максимальном priority выбор случайный, не по created_at."""
    id_a = await _enqueue(priority=_TEST_PRIO)
    id_b = await _enqueue(priority=_TEST_PRIO)
    repo = TaskQueueRepo()

    first_picks: set[int] = set()
    for _ in range(30):
        await _reset_to_queued(id_a, id_b)
        claimed = await repo.claim_next(locked_by="w", task_type_codes=_CODES)
        assert claimed is not None
        assert claimed.id in (id_a, id_b)
        first_picks.add(claimed.id)
        await repo.complete(claimed.id)

    assert id_a in first_picks and id_b in first_picks


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_exhausts_max_tier_before_lower(clean_queue) -> None:
    """Z4/C7: пока max-tier не исчерпан, задачи lower-tier не берутся."""
    high_ids = {await _enqueue(priority=_TEST_PRIO) for _ in range(3)}
    low_ids = {await _enqueue(priority=_TEST_PRIO - 100) for _ in range(2)}
    repo = TaskQueueRepo()

    claimed_high: set[int] = set()
    for _ in range(3):
        claimed = await repo.claim_next(locked_by="w", task_type_codes=_CODES)
        assert claimed is not None
        assert claimed.id in high_ids
        claimed_high.add(claimed.id)
        await repo.complete(claimed.id)

    assert claimed_high == high_ids

    fourth = await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    assert fourth is not None
    assert fourth.id in low_ids


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_skips_not_ready_even_with_higher_priority(clean_queue) -> None:
    """Будущий run_after не участвует в max_prio — берётся готовая с меньшим priority."""
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    await _enqueue(priority=_TEST_PRIO, run_after=future)
    ready_id = await _enqueue(priority=_TEST_PRIO - 50)

    claimed = await TaskQueueRepo().claim_next(locked_by="w", task_type_codes=_CODES)
    assert claimed is not None
    assert claimed.id == ready_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_skips_future_run_after(clean_queue) -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    await _enqueue(run_after=future)

    claimed = await TaskQueueRepo().claim_next(
        locked_by="w", task_type_codes=["__no_such_type__"]
    )
    assert claimed is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_filters_by_task_type_codes(clean_queue) -> None:
    our_id = await _enqueue(task_type_code="parser_add_channel")
    await _enqueue(task_type_code="move_channel", priority=_TEST_PRIO)

    claimed = await TaskQueueRepo().claim_next(
        locked_by="w", task_type_codes=["parser_add_channel"]
    )
    assert claimed is not None
    assert claimed.id == our_id
    assert claimed.task_type_code == "parser_add_channel"


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_no_double_claim_same_task(clean_queue) -> None:
    task_id = await _enqueue()
    repo = TaskQueueRepo()

    first = await repo.claim_next(locked_by="w1", task_type_codes=_CODES)
    assert first is not None and first.id == task_id

    again = await repo.claim_next(locked_by="w2", task_type_codes=_CODES)
    assert again is None or again.id != task_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_expired_lock_is_reclaimable(clean_queue) -> None:
    task_id = await _enqueue()
    repo = TaskQueueRepo()

    claimed = await repo.claim_next(locked_by="w1", task_type_codes=_CODES)
    assert claimed is not None and claimed.id == task_id

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET status = 'retry', "
            "locked_until = now() - interval '1 minute' WHERE id = $1",
            task_id,
        )

    reclaimed = await repo.claim_next(locked_by="w2", task_type_codes=_CODES)
    assert reclaimed is not None
    assert reclaimed.id == task_id
    assert reclaimed.attempt_count == 0


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_claims_get_all_distinct_at_max_priority(clean_queue) -> None:
    ids = {await _enqueue(priority=_TEST_PRIO) for _ in range(5)}
    repo = TaskQueueRepo()

    results = await asyncio.gather(
        *[
            repo.claim_next(locked_by=f"w{i}", task_type_codes=_CODES)
            for i in range(5)
        ]
    )
    claimed_ids = [c.id for c in results if c is not None]

    assert len(claimed_ids) == 5
    assert len(set(claimed_ids)) == 5
    assert set(claimed_ids) == ids


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_reschedule_then_claim_retry(clean_queue) -> None:
    """retry с истёкшим lock снова попадает в пул max_prio + random."""
    task_id = await _enqueue()
    repo = TaskQueueRepo()

    await repo.claim_next(locked_by="w", task_type_codes=_CODES)
    await repo.begin_execution_attempt(task_id)
    status = await repo.reschedule_or_fail(task_id, "temp", retry_delay_seconds=0)
    assert status == "retry"

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET run_after = now() - interval '1 second' "
            "WHERE id = $1",
            task_id,
        )

    reclaimed = await repo.claim_next(locked_by="w2", task_type_codes=_CODES)
    assert reclaimed is not None
    assert reclaimed.id == task_id
    assert reclaimed.attempt_count == 1
