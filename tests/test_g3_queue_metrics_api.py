"""G3 — интеграционные тесты metrics_repo и JSON-контракта §26."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.monitoring.metrics_repo import MetricsRepo
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg, TEST_ISOLATION_PRIORITY
from tests.pg_cleanup import cleanup_queue_test_data

pytestmark = [requires_pg, pytest.mark.integration]

_PREFIX = "test_g3_"
_TASK_TYPE_ADD = "parser_add_channel"
_OP_GET_ENTITY = "get_entity"


async def _cleanup() -> None:
    await cleanup_queue_test_data(
        dedup_key_like=f"{_PREFIX}%",
        session_name_like=f"{_PREFIX}%",
    )


@pytest.fixture
async def g3_clean(pg_pool):
    await _cleanup()
    yield
    await _cleanup()


@pytest.fixture
async def g3_ctx(g3_clean):
    session_name = f"{_PREFIX}{uuid.uuid4().hex}"
    dedup_key = f"{_PREFIX}{uuid.uuid4().hex}"

    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )
        task_type_id = await conn.fetchval(
            "SELECT id FROM task_types WHERE code = $1", _TASK_TYPE_ADD
        )
        op = await conn.fetchrow(
            "SELECT id, rph_limit, reserve_percent FROM resource_op_types "
            "WHERE code = $1",
            _OP_GET_ENTITY,
        )

    enqueue = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code=_TASK_TYPE_ADD,
            dedup_key=dedup_key,
            priority=TEST_ISOLATION_PRIORITY,
            account_id=account_id,
        )
    )

    effective_rph = int(op["rph_limit"] * (1 - float(op["reserve_percent"]) / 100.0))

    return {
        "session_name": session_name,
        "dedup_key": dedup_key,
        "account_id": account_id,
        "task_id": enqueue.task_id,
        "task_type_id": task_type_id,
        "op_type_id": op["id"],
        "effective_rph": effective_rph,
    }


@pytest.mark.asyncio
async def test_fetch_snapshot_structure(g3_clean) -> None:
    snapshot = await MetricsRepo().fetch_snapshot()
    data = snapshot.to_response_dict()

    assert "queue" in data
    assert "accounts" in data
    assert "alerts_preview" in data
    assert "generated_at" in data
    assert data["queue"]["total"] >= 0
    assert isinstance(data["queue"]["by_status"], dict)
    assert isinstance(data["queue"]["by_type"], dict)
    assert isinstance(data["accounts"]["per_op"], list)
    assert isinstance(data["accounts"]["worst_by_account"], list)


@pytest.mark.asyncio
async def test_fetch_snapshot_reflects_queued_task(g3_ctx) -> None:
    before = await MetricsRepo().fetch_snapshot()
    queued_before = before.queue.by_status.get("queued", 0)
    total_before = before.queue.total

    dedup_key = f"{_PREFIX}{uuid.uuid4().hex}"
    await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code=_TASK_TYPE_ADD,
            dedup_key=dedup_key,
            priority=TEST_ISOLATION_PRIORITY,
            account_id=g3_ctx["account_id"],
        )
    )

    after = await MetricsRepo().fetch_snapshot()
    assert after.queue.by_status.get("queued", 0) >= queued_before + 1
    assert after.queue.total >= total_before + 1
    by_type = after.queue.by_type.get(_TASK_TYPE_ADD, {})
    assert by_type.get("queued", 0) >= 1


@pytest.mark.asyncio
async def test_fetch_snapshot_reflects_cooldown(g3_ctx) -> None:
    before = await MetricsRepo().fetch_snapshot()
    cooldown_before = before.accounts.in_cooldown

    until = datetime.now(timezone.utc) + timedelta(hours=1)
    assert await AccountsRepo().set_cooldown(g3_ctx["session_name"], until) is True

    after = await MetricsRepo().fetch_snapshot()
    assert after.accounts.in_cooldown >= cooldown_before + 1


@pytest.mark.asyncio
async def test_fetch_snapshot_reflects_resource_exhaustion(g3_ctx) -> None:
    await ResourceUsageRepo().insert(
        account_id=g3_ctx["account_id"],
        op_type_id=g3_ctx["op_type_id"],
        task_id=g3_ctx["task_id"],
        task_type_id=g3_ctx["task_type_id"],
        units=g3_ctx["effective_rph"],
    )

    snapshot = await MetricsRepo().fetch_snapshot()
    per_op = [
        row
        for row in snapshot.accounts.per_op
        if row.account_id == g3_ctx["account_id"]
        and row.op_type_id == g3_ctx["op_type_id"]
    ]
    assert len(per_op) >= 1
    assert per_op[0].available_resource <= 0

    worst = [
        row
        for row in snapshot.accounts.worst_by_account
        if row.account_id == g3_ctx["account_id"]
    ]
    assert len(worst) >= 1
    assert worst[0].any_op_exhausted is True
    assert snapshot.accounts.without_resource >= 1


@pytest.mark.asyncio
async def test_fetch_snapshot_high_postpone_count(g3_clean) -> None:
    dedup_key = f"{_PREFIX}postpone_{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO task_queue (
                task_type_id, task_type_code, status, priority, dedup_key,
                max_attempts, postpone_count
            )
            SELECT id, code, 'scheduled', $2, $3, max_attempts, 999
            FROM task_types WHERE code = $1
            """,
            _TASK_TYPE_ADD,
            TEST_ISOLATION_PRIORITY,
            dedup_key,
        )

    snapshot = await MetricsRepo().fetch_snapshot()
    assert snapshot.alerts_preview.high_postpone_count >= 1


@pytest.mark.asyncio
async def test_to_response_dict_matches_v_queue_metrics(g3_ctx) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET created_at = now() - interval '120 seconds' "
            "WHERE id = $1",
            g3_ctx["task_id"],
        )
        row = await conn.fetchrow("SELECT * FROM v_queue_metrics")

    snapshot = await MetricsRepo().fetch_snapshot()
    data = snapshot.to_response_dict()

    assert data["queue"]["total"] == int(row["queue_size_total"])
    assert data["queue"]["stuck_count"] == int(row["stuck_tasks_count"])
    assert data["queue"]["done_last_5_min"] == int(row["done_tasks_last_5_min"])
    assert data["queue"]["oldest_queued_age_seconds"] == int(
        row["oldest_queued_task_age_seconds"]
    )
    assert int(data["queue"]["oldest_queued_age_seconds"]) >= 100
