"""F4 — unit-тесты CollectExtraDataProducer (без PostgreSQL)."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app_balance.queue.ops_catalog import COLLECT_EXTRA_DATA
from app_balance.queue.per_op_reading import TaskType
from app_balance.queue.producers.base import ProduceResult
from app_balance.queue.producers.collect_extra_data import (
    CREATED_BY,
    CollectExtraDataProducer,
)
from app_balance.queue.source_channels import PendingChannel
from app_balance.queue.task_queue import EnqueueInput


def _task_type(
    *,
    is_enabled: bool = True,
    target_queue_size: int | None = 20,
) -> TaskType:
    return TaskType(
        id=4,
        code=COLLECT_EXTRA_DATA,
        name=COLLECT_EXTRA_DATA,
        description=None,
        is_enabled=is_enabled,
        default_priority=200,
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


def _producer(
    *,
    task_type: TaskType | None = None,
    pending: list[PendingChannel] | None = None,
    capacity: int | None = 5,
) -> CollectExtraDataProducer:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=task_type)

    channels = AsyncMock()
    channels.list_pending_collect = AsyncMock(return_value=pending or [])

    producer = CollectExtraDataProducer(task_types=task_types, channels=channels)
    producer.remaining_capacity = AsyncMock(return_value=capacity)
    producer.enqueue_if_room = AsyncMock(
        return_value=ProduceResult(created=True, task_id=1)
    )
    return producer


@pytest.mark.asyncio
async def test_produce_returns_empty_when_task_type_missing() -> None:
    producer = _producer(task_type=None)
    assert await producer.produce() == []
    producer._channels.list_pending_collect.assert_not_awaited()


@pytest.mark.asyncio
async def test_produce_returns_empty_when_task_type_disabled() -> None:
    producer = _producer(task_type=_task_type(is_enabled=False))
    assert await producer.produce() == []
    producer._channels.list_pending_collect.assert_not_awaited()


@pytest.mark.asyncio
async def test_produce_returns_empty_when_capacity_zero() -> None:
    producer = _producer(task_type=_task_type(), capacity=0)
    assert await producer.produce() == []
    producer._channels.list_pending_collect.assert_not_awaited()


@pytest.mark.asyncio
async def test_produce_returns_empty_when_no_pending_channels() -> None:
    producer = _producer(task_type=_task_type(), pending=[])
    assert await producer.produce() == []
    producer._channels.list_pending_collect.assert_awaited_once_with(limit=5)


@pytest.mark.asyncio
async def test_produce_enqueues_pending_channels_within_capacity() -> None:
    pending = [
        PendingChannel(channel_id=10, account_id=100),
        PendingChannel(channel_id=11, account_id=101),
    ]
    producer = _producer(task_type=_task_type(), pending=pending, capacity=5)

    results = await producer.produce()

    assert len(results) == 2
    assert producer.enqueue_if_room.await_count == 2
    first_call = producer.enqueue_if_room.await_args_list[0].args[0]
    assert first_call == EnqueueInput(
        task_type_code=COLLECT_EXTRA_DATA,
        channel_id=10,
        account_id=100,
        dedup_key=f"{COLLECT_EXTRA_DATA}:10",
        created_by=CREATED_BY,
    )
    second_call = producer.enqueue_if_room.await_args_list[1].args[0]
    assert second_call.channel_id == 11
    assert second_call.account_id == 101
    assert second_call.dedup_key == f"{COLLECT_EXTRA_DATA}:11"
    assert second_call.created_by == CREATED_BY


@pytest.mark.asyncio
async def test_produce_uses_default_batch_when_capacity_unlimited() -> None:
    producer = _producer(task_type=_task_type(target_queue_size=None), capacity=None)
    await producer.produce()
    producer._channels.list_pending_collect.assert_awaited_once_with(limit=20)


@pytest.mark.asyncio
async def test_produce_passes_through_enqueue_results() -> None:
    pending = [PendingChannel(channel_id=42, account_id=7)]
    producer = _producer(task_type=_task_type(), pending=pending, capacity=1)
    producer.enqueue_if_room = AsyncMock(
        return_value=ProduceResult(
            created=False,
            task_id=None,
            existing_task_id=999,
            skipped_reason="duplicate",
        )
    )

    results = await producer.produce()

    assert len(results) == 1
    assert results[0].skipped_reason == "duplicate"
    assert results[0].existing_task_id == 999


@pytest.mark.asyncio
async def test_produce_logs_warning_on_fatal_history(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pending = [PendingChannel(channel_id=42, account_id=7)]
    producer = _producer(task_type=_task_type(), pending=pending, capacity=1)
    producer.enqueue_if_room = AsyncMock(
        return_value=ProduceResult(
            created=False,
            task_id=None,
            existing_task_id=999,
            skipped_reason="fatal_history",
            fatal_error_code="banned",
        )
    )

    with caplog.at_level(
        "WARNING", logger="app_balance.queue.producers.collect_extra_data"
    ):
        results = await producer.produce()

    assert len(results) == 1
    assert results[0].skipped_reason == "fatal_history"
    assert any(
        "не поставлен в очередь" in record.message and "id=42" in record.message
        for record in caplog.records
    )
