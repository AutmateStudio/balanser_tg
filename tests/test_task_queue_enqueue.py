"""B3 — интеграционные тесты TaskQueueRepo (enqueue + dedup)."""
from __future__ import annotations

import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.task_queue import (
    EnqueueInput,
    TaskQueueRepo,
    UnknownTaskTypeError,
)
from tests.conftest import requires_pg

_DEDUP_PREFIX = "test_b3_"


def _unique_key() -> str:
    return f"{_DEDUP_PREFIX}{uuid.uuid4().hex}"


@pytest.fixture
async def clean_queue(pg_pool):
    """Удаляет тестовые строки task_queue до и после теста."""
    async def _cleanup() -> None:
        async with db.acquire() as conn:
            await conn.execute(
                "DELETE FROM task_queue WHERE dedup_key LIKE $1",
                f"{_DEDUP_PREFIX}%",
            )

    await _cleanup()
    yield
    await _cleanup()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_enqueue_creates_task(clean_queue) -> None:
    repo = TaskQueueRepo()
    result = await repo.enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            payload={"channel_ref": "@demo"},
            dedup_key=_unique_key(),
        )
    )

    assert result.created is True
    assert result.task_id is not None

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, priority, max_attempts, task_type_code, payload "
            "FROM task_queue WHERE id = $1",
            result.task_id,
        )
    assert row["status"] == "queued"
    assert row["task_type_code"] == "parser_add_channel"
    # priority/max_attempts подтянуты из task_types (default_priority=500)
    assert row["priority"] == 500
    assert row["max_attempts"] >= 1


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_enqueue_dedup_skips_second(clean_queue) -> None:
    repo = TaskQueueRepo()
    key = _unique_key()

    first = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )
    second = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )

    assert first.created is True
    assert second.created is False
    assert second.existing_task_id == first.task_id

    async with db.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM task_queue WHERE dedup_key = $1", key
        )
    assert count == 1


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_enqueue_different_dedup_keys(clean_queue) -> None:
    repo = TaskQueueRepo()
    r1 = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=_unique_key())
    )
    r2 = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=_unique_key())
    )

    assert r1.created is True and r2.created is True
    assert r1.task_id != r2.task_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_enqueue_same_key_after_done(clean_queue) -> None:
    repo = TaskQueueRepo()
    key = _unique_key()

    first = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )
    # Задача больше не активна — partial unique перестаёт действовать.
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET status = 'done' WHERE id = $1", first.task_id
        )

    second = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )

    assert second.created is True
    assert second.task_id != first.task_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_enqueue_null_dedup_allows_duplicates(clean_queue) -> None:
    repo = TaskQueueRepo()
    # Без dedup_key защита не действует — обе создаются.
    r1 = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=None)
    )
    r2 = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=None)
    )
    try:
        assert r1.created is True and r2.created is True
        assert r1.task_id != r2.task_id
    finally:
        async with db.acquire() as conn:
            await conn.execute(
                "DELETE FROM task_queue WHERE id = ANY($1::bigint[])",
                [r1.task_id, r2.task_id],
            )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_enqueue_unknown_task_type_raises(clean_queue) -> None:
    repo = TaskQueueRepo()
    with pytest.raises(UnknownTaskTypeError):
        await repo.enqueue(EnqueueInput(task_type_code="no_such_type_xyz"))


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_enqueue_skips_after_fatal_failure(clean_queue) -> None:
    """B12: канал, terminal failed с постоянной причиной (banned), не re-enqueue."""
    repo = TaskQueueRepo()
    key = _unique_key()

    first = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET status = 'failed', last_error = 'banned:test' "
            "WHERE id = $1",
            first.task_id,
        )

    second = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )

    assert second.created is False
    assert second.skipped_reason == "fatal_history"
    assert second.fatal_error_code == "banned"
    assert second.existing_task_id == first.task_id

    async with db.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM task_queue WHERE dedup_key = $1", key
        )
    assert count == 1


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_enqueue_allows_retry_after_transient_failure(clean_queue) -> None:
    """B12: retryable-код (flood_wait) в failed НЕ блокирует повторную постановку."""
    repo = TaskQueueRepo()
    key = _unique_key()

    first = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET status = 'failed', last_error = 'flood_wait:120' "
            "WHERE id = $1",
            first.task_id,
        )

    second = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )

    assert second.created is True
    assert second.skipped_reason is None
    assert second.task_id != first.task_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_enqueue_force_retry_bypasses_fatal_history(clean_queue) -> None:
    """B12: skip_known_fatal=False — ручной override оператора."""
    repo = TaskQueueRepo()
    key = _unique_key()

    first = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET status = 'failed', last_error = 'channel_private' "
            "WHERE id = $1",
            first.task_id,
        )

    second = await repo.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key),
        skip_known_fatal=False,
    )

    assert second.created is True
    assert second.task_id != first.task_id
