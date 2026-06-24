"""B9 — интеграционные тесты TaskAttemptsRepo (insert / finish)."""
from __future__ import annotations

import uuid

import asyncpg
import pytest

from app_balance.queue import db
from app_balance.queue.task_attempts import TaskAttemptsRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from tests.conftest import TEST_ISOLATION_PRIORITY, requires_pg
from tests.pg_cleanup import cleanup_queue_test_data
from tests.queue_integration_helpers import insert_test_account

_PREFIX = "test_b9_"


@pytest.fixture
async def attempts_ctx(pg_pool):
    """Аккаунт + задача для записи попыток. Чистит за собой."""
    session_name = f"{_PREFIX}{uuid.uuid4().hex}"
    dedup_key = f"{_PREFIX}{uuid.uuid4().hex}"

    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_PREFIX}%",
            session_name_like=f"{_PREFIX}%",
        )

    await _cleanup()

    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )
        task_type_id = await conn.fetchval(
            "SELECT id FROM task_types WHERE code = 'parser_add_channel'"
        )

    enqueue = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=dedup_key,
            priority=TEST_ISOLATION_PRIORITY,
        )
    )

    yield {
        "account_id": account_id,
        "task_id": enqueue.task_id,
        "task_type_id": task_type_id,
    }
    await _cleanup()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_insert_finish_success(attempts_ctx) -> None:
    repo = TaskAttemptsRepo()
    attempt_id = await repo.insert(
        task_id=attempts_ctx["task_id"],
        task_type_id=attempts_ctx["task_type_id"],
        account_id=attempts_ctx["account_id"],
        attempt_number=1,
    )
    assert await repo.finish(attempt_id, status="success") is True

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT attempt_number, status, finished_at FROM task_attempts WHERE id = $1",
            attempt_id,
        )
    assert row is not None
    assert row["attempt_number"] == 1
    assert row["status"] == "success"
    assert row["finished_at"] is not None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_attempt_number_monotonic(attempts_ctx) -> None:
    queue = TaskQueueRepo()
    repo = TaskAttemptsRepo()
    task_id = attempts_ctx["task_id"]

    claimed1 = await queue.claim_by_id(task_id, locked_by="b9-worker")
    assert claimed1 is not None, "задача недоступна для claim (shared PG / run_after)"
    attempt_number_1 = await queue.begin_execution_attempt(task_id)
    id_1 = await repo.insert(
        task_id=task_id,
        task_type_id=attempts_ctx["task_type_id"],
        account_id=attempts_ctx["account_id"],
        attempt_number=attempt_number_1,
    )
    await repo.finish(id_1, status="error", error_code="transient")

    status = await queue.reschedule_or_fail(task_id, "temp", retry_delay_seconds=0)
    assert status == "retry"

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET run_after = now() - interval '1 second' WHERE id = $1",
            task_id,
        )

    reclaimed = await queue.claim_by_id(task_id, locked_by="b9-worker-2")
    assert reclaimed is not None, "retry-задача недоступна для reclaim (shared PG / run_after)"

    attempt_number_2 = await queue.begin_execution_attempt(task_id)
    id_2 = await repo.insert(
        task_id=task_id,
        task_type_id=attempts_ctx["task_type_id"],
        account_id=attempts_ctx["account_id"],
        attempt_number=attempt_number_2,
    )
    await repo.finish(id_2, status="success")

    assert attempt_number_1 == 1
    assert attempt_number_2 == 2

    async with db.acquire() as conn:
        numbers = await conn.fetch(
            "SELECT attempt_number FROM task_attempts WHERE task_id = $1 ORDER BY attempt_number",
            task_id,
        )
    assert [row["attempt_number"] for row in numbers] == [1, 2]


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_attempt_number_raises(attempts_ctx) -> None:
    repo = TaskAttemptsRepo()
    kwargs = {
        "task_id": attempts_ctx["task_id"],
        "task_type_id": attempts_ctx["task_type_id"],
        "account_id": attempts_ctx["account_id"],
        "attempt_number": 1,
    }
    await repo.insert(**kwargs)

    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.insert(**kwargs)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_finish_idempotent(attempts_ctx) -> None:
    repo = TaskAttemptsRepo()
    attempt_id = await repo.insert(
        task_id=attempts_ctx["task_id"],
        task_type_id=attempts_ctx["task_type_id"],
        account_id=attempts_ctx["account_id"],
        attempt_number=1,
    )
    assert await repo.finish(attempt_id, status="success") is True
    assert await repo.finish(attempt_id, status="error", error_code="late") is False

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, error_code FROM task_attempts WHERE id = $1",
            attempt_id,
        )
    assert row is not None
    assert row["status"] == "success"
    assert row["error_code"] is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_finish_error_with_codes(attempts_ctx) -> None:
    repo = TaskAttemptsRepo()
    attempt_id = await repo.insert(
        task_id=attempts_ctx["task_id"],
        task_type_id=attempts_ctx["task_type_id"],
        account_id=attempts_ctx["account_id"],
        attempt_number=1,
    )
    assert (
        await repo.finish(
            attempt_id,
            status="error",
            error_code="flood_wait",
            error_message="Flood wait 120 seconds",
        )
        is True
    )

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, error_code, error_message, finished_at "
            "FROM task_attempts WHERE id = $1",
            attempt_id,
        )
    assert row is not None
    assert row["status"] == "error"
    assert row["error_code"] == "flood_wait"
    assert row["error_message"] == "Flood wait 120 seconds"
    assert row["finished_at"] is not None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_insert_dual_account_fields(attempts_ctx) -> None:
    """B9: insert сохраняет source_account_id / target_account_id."""
    source_id, _ = await insert_test_account(prefix=f"{_PREFIX}src_")
    target_id, _ = await insert_test_account(prefix=f"{_PREFIX}tgt_")
    repo = TaskAttemptsRepo()
    attempt_id = await repo.insert(
        task_id=attempts_ctx["task_id"],
        task_type_id=attempts_ctx["task_type_id"],
        account_id=attempts_ctx["account_id"],
        attempt_number=1,
        source_account_id=source_id,
        target_account_id=target_id,
    )
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT account_id, source_account_id, target_account_id
            FROM task_attempts WHERE id = $1
            """,
            attempt_id,
        )
    assert row is not None
    assert row["account_id"] == attempts_ctx["account_id"]
    assert row["source_account_id"] == source_id
    assert row["target_account_id"] == target_id
