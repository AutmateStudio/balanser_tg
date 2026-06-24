"""B8 — интеграционные тесты ResourceUsageRepo (insert / count / availability per-op)."""
from __future__ import annotations

import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_b8_"


@pytest.fixture
async def usage_ctx(pg_pool):
    """Аккаунт + задача + op_type_id + task_type_id для записи расхода. Чистит за собой."""
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
        op_type_id = await conn.fetchval(
            "SELECT id FROM resource_op_types WHERE code = 'get_entity'"
        )

    enqueue = await TaskQueueRepo().enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=dedup_key)
    )

    yield {
        "account_id": account_id,
        "task_id": enqueue.task_id,
        "task_type_id": task_type_id,
        "op_type_id": op_type_id,
    }
    await _cleanup()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_insert_and_count_last_hour(usage_ctx) -> None:
    repo = ResourceUsageRepo()
    before = await repo.count_last_hour(usage_ctx["account_id"], usage_ctx["op_type_id"])

    await repo.insert(
        account_id=usage_ctx["account_id"],
        op_type_id=usage_ctx["op_type_id"],
        task_id=usage_ctx["task_id"],
        task_type_id=usage_ctx["task_type_id"],
        units=2,
    )
    await repo.insert(
        account_id=usage_ctx["account_id"],
        op_type_id=usage_ctx["op_type_id"],
        task_id=usage_ctx["task_id"],
        task_type_id=usage_ctx["task_type_id"],
        units=3,
    )

    after = await repo.count_last_hour(usage_ctx["account_id"], usage_ctx["op_type_id"])
    assert after - before == 5


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_count_excludes_rows_older_than_hour(usage_ctx) -> None:
    repo = ResourceUsageRepo()

    # Старая запись (2 часа назад) не должна учитываться.
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO account_resource_usage "
            "(account_id, op_type_id, task_id, task_type_id, units, created_at) "
            "VALUES ($1, $2, $3, $4, 99, now() - interval '2 hours')",
            usage_ctx["account_id"],
            usage_ctx["op_type_id"],
            usage_ctx["task_id"],
            usage_ctx["task_type_id"],
        )

    await repo.insert(
        account_id=usage_ctx["account_id"],
        op_type_id=usage_ctx["op_type_id"],
        task_id=usage_ctx["task_id"],
        task_type_id=usage_ctx["task_type_id"],
        units=4,
    )

    count = await repo.count_last_hour(usage_ctx["account_id"], usage_ctx["op_type_id"])
    assert count == 4


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_op_availability_decreases_after_usage(usage_ctx) -> None:
    repo = ResourceUsageRepo()
    before = await repo.op_availability(usage_ctx["account_id"], usage_ctx["op_type_id"])
    assert before is not None
    assert before.used_last_hour == 0

    await repo.insert(
        account_id=usage_ctx["account_id"],
        op_type_id=usage_ctx["op_type_id"],
        task_id=usage_ctx["task_id"],
        task_type_id=usage_ctx["task_type_id"],
        units=1,
    )

    after = await repo.op_availability(usage_ctx["account_id"], usage_ctx["op_type_id"])
    assert after is not None
    assert after.used_last_hour == before.used_last_hour + 1
    assert after.available_resource == before.available_resource - 1
    assert after.available_resource_percent <= before.available_resource_percent


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_worst_available_percent_in_range(usage_ctx) -> None:
    worst = await ResourceUsageRepo().worst_available_percent(usage_ctx["account_id"])
    assert worst is not None
    assert 0.0 <= worst <= 100.0
