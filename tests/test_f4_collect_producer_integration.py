"""F4 — integration: CollectExtraDataProducer на shared PG."""
from __future__ import annotations

import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.ops_catalog import COLLECT_EXTRA_DATA
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.producers.collect_extra_data import CollectExtraDataProducer
from app_balance.queue.producers.base import count_active_tasks
from app_balance.queue.task_queue import ACTIVE_STATUSES
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data
from tests.queue_integration_helpers import insert_test_account

_PREFIX = "test_f4_collect_"


async def _cleanup() -> None:
    async with db.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM task_queue
            WHERE dedup_key LIKE $1
               OR created_by = 'collect_extra_data_producer'
            """,
            f"{COLLECT_EXTRA_DATA}:%",
        )
        await conn.execute(
            """
            DELETE FROM task_queue
            WHERE channel_id IN (
                SELECT id FROM source_channels WHERE external_channel_id LIKE $1
            )
            """,
            f"{_PREFIX}%",
        )
        await conn.execute(
            "DELETE FROM source_channels WHERE external_channel_id LIKE $1",
            f"{_PREFIX}%",
        )
        await conn.execute(
            "DELETE FROM platforms WHERE code LIKE $1",
            f"{_PREFIX}%",
        )
    await cleanup_queue_test_data(
        dedup_key_like=f"{_PREFIX}%",
        session_name_like=f"{_PREFIX}%",
    )


async def _enable_collect_task_type() -> bool:
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_enabled FROM task_types WHERE code = $1",
            COLLECT_EXTRA_DATA,
        )
        if row is None:
            pytest.skip("collect_extra_data отсутствует в seed")
        was_enabled = bool(row["is_enabled"])
        if not was_enabled:
            await conn.execute(
                "UPDATE task_types SET is_enabled = true WHERE code = $1",
                COLLECT_EXTRA_DATA,
            )
        return was_enabled


async def _restore_collect_enabled(was_enabled: bool) -> None:
    if was_enabled:
        return
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_types SET is_enabled = false WHERE code = $1",
            COLLECT_EXTRA_DATA,
        )


async def _insert_pending_channel(*, account_id: int, suffix: str) -> int:
    async with db.acquire() as conn:
        platform_id = await conn.fetchval(
            "INSERT INTO platforms (code, name) VALUES ($1, $2) RETURNING id",
            f"{_PREFIX}{suffix}",
            "F4 test platform",
        )
        return int(
            await conn.fetchval(
                """
                INSERT INTO source_channels (
                    platform_id, external_channel_id, name,
                    assigned_account_id, extra_data_collected
                ) VALUES ($1, $2, $3, $4, false)
                RETURNING id
                """,
                platform_id,
                f"{_PREFIX}{suffix}",
                f"channel {suffix}",
                account_id,
            )
        )


@pytest.fixture
async def f4_clean(pg_pool):
    await _cleanup()
    yield
    await _cleanup()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_produce_creates_tasks_for_pending_channels(f4_clean) -> None:
    was_enabled = await _enable_collect_task_type()
    try:
        account_id, _ = await insert_test_account(prefix=_PREFIX)
        suffix_a = uuid.uuid4().hex
        suffix_b = uuid.uuid4().hex
        channel_a = await _insert_pending_channel(account_id=account_id, suffix=suffix_a)
        channel_b = await _insert_pending_channel(account_id=account_id, suffix=suffix_b)

        producer = CollectExtraDataProducer()
        results = await producer.produce()

        created = [r for r in results if r.created]
        assert len(created) == 2
        assert all(r.task_id is not None for r in created)

        async with db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, channel_id, account_id, dedup_key, created_by
                FROM task_queue
                WHERE task_type_code = $1
                  AND channel_id = ANY($2::bigint[])
                  AND status = ANY($3::task_status[])
                ORDER BY channel_id
                """,
                COLLECT_EXTRA_DATA,
                [channel_a, channel_b],
                list(ACTIVE_STATUSES),
            )
        assert len(rows) == 2
        assert {row["channel_id"] for row in rows} == {channel_a, channel_b}
        assert all(row["account_id"] == account_id for row in rows)
        assert all(row["created_by"] == "collect_extra_data_producer" for row in rows)
        dedup_keys = {row["dedup_key"] for row in rows}
        assert dedup_keys == {
            f"{COLLECT_EXTRA_DATA}:{channel_a}",
            f"{COLLECT_EXTRA_DATA}:{channel_b}",
        }
    finally:
        await _restore_collect_enabled(was_enabled)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_produce_does_not_create_duplicates(f4_clean) -> None:
    was_enabled = await _enable_collect_task_type()
    try:
        account_id, _ = await insert_test_account(prefix=_PREFIX)
        channel_id = await _insert_pending_channel(
            account_id=account_id,
            suffix=uuid.uuid4().hex,
        )

        producer = CollectExtraDataProducer()
        first = await producer.produce()
        second = await producer.produce()

        assert len([r for r in first if r.created]) == 1
        assert all(not r.created for r in second)
        assert all(r.skipped_reason == "duplicate" for r in second)

        async with db.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM task_queue
                WHERE task_type_code = $1
                  AND channel_id = $2
                  AND status = ANY($3::task_status[])
                """,
                COLLECT_EXTRA_DATA,
                channel_id,
                list(ACTIVE_STATUSES),
            )
        assert count == 1
    finally:
        await _restore_collect_enabled(was_enabled)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_produce_stops_at_target_queue_size(f4_clean) -> None:
    was_enabled = await _enable_collect_task_type()
    try:
        task_type = await TaskTypesRepo().get_by_code(COLLECT_EXTRA_DATA)
        assert task_type is not None
        assert task_type.target_queue_size is not None

        account_id, _ = await insert_test_account(prefix=_PREFIX)
        for _ in range(task_type.target_queue_size + 3):
            await _insert_pending_channel(
                account_id=account_id,
                suffix=uuid.uuid4().hex,
            )

        producer = CollectExtraDataProducer()
        results = await producer.produce()

        created = [r for r in results if r.created]
        assert len(created) == task_type.target_queue_size

        active_after = await count_active_tasks(task_type.id)
        assert active_after == task_type.target_queue_size
    finally:
        await _restore_collect_enabled(was_enabled)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_produce_returns_empty_when_queue_already_full(f4_clean) -> None:
    was_enabled = await _enable_collect_task_type()
    try:
        task_type = await TaskTypesRepo().get_by_code(COLLECT_EXTRA_DATA)
        assert task_type is not None
        assert task_type.target_queue_size is not None

        account_id, _ = await insert_test_account(prefix=_PREFIX)
        async with db.acquire() as conn:
            for i in range(task_type.target_queue_size):
                await conn.execute(
                    """
                    INSERT INTO task_queue (
                        task_type_id, task_type_code, status, priority,
                        account_id, payload, dedup_key, max_attempts, run_after
                    ) VALUES (
                        $1, $2, 'queued', 2000000000,
                        $3, '{}'::jsonb, $4, 5, now()
                    )
                    """,
                    task_type.id,
                    COLLECT_EXTRA_DATA,
                    account_id,
                    f"{_PREFIX}prefill_{i}_{uuid.uuid4().hex}",
                )

        await _insert_pending_channel(
            account_id=account_id,
            suffix=uuid.uuid4().hex,
        )

        producer = CollectExtraDataProducer()
        results = await producer.produce()

        assert results == []
        active_after = await count_active_tasks(task_type.id)
        assert active_after == task_type.target_queue_size
    finally:
        await _restore_collect_enabled(was_enabled)
