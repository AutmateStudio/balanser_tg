"""F5 — unit-тесты UpdateChannelProducer / list_stale_for_update (без PostgreSQL)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app_balance.queue.per_op_reading import TaskType
from app_balance.queue.producers.base import ProduceResult
from app_balance.queue.producers.update_channel import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_STALE_AFTER_SECONDS,
    UpdateChannelProducer,
)
from app_balance.queue.source_channels import (
    StaleChannel,
    _LIST_STALE_FOR_UPDATE_SQL,
)
from app_balance.queue.task_queue import EnqueueInput, EnqueueResult


def _task_type(
    *,
    code: str = "update_channel",
    target_queue_size: int | None = 20,
    is_enabled: bool = True,
) -> TaskType:
    return TaskType(
        id=4,
        code=code,
        name=code,
        description=None,
        is_enabled=is_enabled,
        default_priority=50,
        min_available_resource_percent=90,
        requires_specific_account=False,
        uses_two_accounts=False,
        max_attempts=5,
        retry_delay_seconds=60,
        retry_backoff_multiplier=Decimal("2"),
        max_retry_delay_seconds=1800,
        target_queue_size=target_queue_size,
        max_postpone_count=100,
        task_timeout_seconds=600,
        created_at=None,
        updated_at=None,
        ops=(),
    )


def test_stale_select_sql_uses_index_predicates() -> None:
    sql = _LIST_STALE_FOR_UPDATE_SQL
    assert "assigned_account_id IS NOT NULL" in sql
    assert "is_active = true" in sql
    assert "ORDER BY last_updated_at ASC NULLS FIRST" in sql


@pytest.mark.asyncio
async def test_list_stale_for_update_queries_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app_balance.queue.source_channels import SourceChannelsRepo

    conn = AsyncMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"id": 10, "assigned_account_id": 100, "last_updated_at": None},
            {"id": 11, "assigned_account_id": 101, "last_updated_at": "2020-01-01"},
        ]
    )

    @asynccontextmanager
    async def fake_acquire():
        yield conn

    monkeypatch.setattr(
        "app_balance.queue.source_channels.acquire", fake_acquire
    )

    repo = SourceChannelsRepo()
    result = await repo.list_stale_for_update(limit=5, stale_after_seconds=100)

    assert result == [
        StaleChannel(id=10, account_id=100, last_updated_at=None),
        StaleChannel(id=11, account_id=101, last_updated_at="2020-01-01"),
    ]
    conn.fetch.assert_awaited_once_with(_LIST_STALE_FOR_UPDATE_SQL, 100, 5)


def test_producer_defaults() -> None:
    assert DEFAULT_STALE_AFTER_SECONDS == 2_592_000
    assert DEFAULT_BATCH_SIZE > 0


@pytest.mark.asyncio
async def test_produce_returns_empty_when_type_disabled() -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_task_type(is_enabled=False))
    channels = AsyncMock()
    channels.list_stale_for_update = AsyncMock()

    producer = UpdateChannelProducer(task_types=task_types, channels=channels)
    result = await producer.produce()

    assert result == []
    channels.list_stale_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_produce_returns_empty_when_type_missing() -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=None)
    channels = AsyncMock()
    channels.list_stale_for_update = AsyncMock()

    producer = UpdateChannelProducer(task_types=task_types, channels=channels)
    result = await producer.produce()

    assert result == []
    channels.list_stale_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_produce_returns_empty_when_queue_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_task_type(target_queue_size=20))
    channels = AsyncMock()
    channels.list_stale_for_update = AsyncMock()

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=20),
    )

    producer = UpdateChannelProducer(task_types=task_types, channels=channels)
    result = await producer.produce()

    assert result == []
    channels.list_stale_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_produce_enqueues_stale_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_task_type(target_queue_size=20))

    channels = AsyncMock()
    channels.list_stale_for_update = AsyncMock(
        return_value=[
            StaleChannel(id=10, account_id=100, last_updated_at=None),
            StaleChannel(id=11, account_id=101, last_updated_at=None),
        ]
    )

    task_queue = AsyncMock()
    task_queue.enqueue = AsyncMock(
        side_effect=[
            EnqueueResult(created=True, task_id=101),
            EnqueueResult(created=True, task_id=102),
        ]
    )

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=5),
    )

    producer = UpdateChannelProducer(
        task_queue=task_queue,
        task_types=task_types,
        channels=channels,
        stale_after_seconds=12345,
    )
    result = await producer.produce()

    assert result == [
        ProduceResult(created=True, task_id=101),
        ProduceResult(created=True, task_id=102),
    ]
    # remaining_capacity = 20 - 5 = 15 → limit пробрасывается в выборку
    channels.list_stale_for_update.assert_awaited_once_with(
        limit=15, stale_after_seconds=12345
    )

    first_call: EnqueueInput = task_queue.enqueue.await_args_list[0].args[0]
    assert first_call.task_type_code == "update_channel"
    assert first_call.channel_id == 10
    assert first_call.account_id == 100
    assert first_call.dedup_key == "update_channel:10"
    assert first_call.created_by == "update_channel_producer"
    assert first_call.payload == {"channel_id": 10}


@pytest.mark.asyncio
async def test_produce_uses_default_batch_when_unlimited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(
        return_value=_task_type(target_queue_size=None)
    )

    channels = AsyncMock()
    channels.list_stale_for_update = AsyncMock(return_value=[])

    producer = UpdateChannelProducer(task_types=task_types, channels=channels)
    result = await producer.produce()

    assert result == []
    channels.list_stale_for_update.assert_awaited_once_with(
        limit=DEFAULT_BATCH_SIZE, stale_after_seconds=DEFAULT_STALE_AFTER_SECONDS
    )


@pytest.mark.asyncio
async def test_produce_propagates_skipped_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_task_type(target_queue_size=20))

    channels = AsyncMock()
    channels.list_stale_for_update = AsyncMock(
        return_value=[StaleChannel(id=10, account_id=100, last_updated_at=None)]
    )

    task_queue = AsyncMock()
    task_queue.enqueue = AsyncMock(
        return_value=EnqueueResult(created=False, task_id=None, existing_task_id=55)
    )

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=0),
    )

    producer = UpdateChannelProducer(
        task_queue=task_queue, task_types=task_types, channels=channels
    )
    result = await producer.produce()

    assert result == [
        ProduceResult(
            created=False,
            task_id=None,
            existing_task_id=55,
            skipped_reason="duplicate",
        )
    ]


@pytest.mark.asyncio
async def test_produce_logs_warning_on_fatal_history(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_task_type(target_queue_size=20))

    channels = AsyncMock()
    channels.list_stale_for_update = AsyncMock(
        return_value=[StaleChannel(id=10, account_id=100, last_updated_at=None)]
    )

    task_queue = AsyncMock()
    task_queue.enqueue = AsyncMock(
        return_value=EnqueueResult(
            created=False,
            task_id=None,
            existing_task_id=77,
            skipped_reason="fatal_history",
            fatal_error_code="channel_private",
        )
    )

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=0),
    )

    producer = UpdateChannelProducer(
        task_queue=task_queue, task_types=task_types, channels=channels
    )
    with caplog.at_level(
        "WARNING", logger="app_balance.queue.producers.update_channel"
    ):
        result = await producer.produce()

    assert result == [
        ProduceResult(
            created=False,
            task_id=None,
            existing_task_id=77,
            skipped_reason="fatal_history",
            fatal_error_code="channel_private",
        )
    ]
    assert any(
        "не поставлен в очередь" in record.message and "id=10" in record.message
        for record in caplog.records
    )
