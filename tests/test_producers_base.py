"""F1 — unit-тесты BaseProducer / enqueue_if_room (без PostgreSQL)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app_balance.queue.per_op_reading import TaskType
from app_balance.queue.producers.base import (
    BaseProducer,
    ProduceResult,
    _COUNT_ACTIVE_BY_TYPE_SQL,
    count_active_tasks,
)
from app_balance.queue.task_queue import (
    ACTIVE_STATUSES,
    EnqueueInput,
    EnqueueResult,
    TaskQueueRepo,
    UnknownTaskTypeError,
)


def _task_type(
    *,
    code: str = "move_channel",
    target_queue_size: int | None = 20,
    is_enabled: bool = True,
) -> TaskType:
    return TaskType(
        id=3,
        code=code,
        name=code,
        description=None,
        is_enabled=is_enabled,
        default_priority=100,
        min_available_resource_percent=80,
        requires_specific_account=False,
        uses_two_accounts=True,
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


class _StubProducer(BaseProducer):
    """Конкретная реализация для тестов abstract produce()."""

    async def produce(self) -> list[ProduceResult]:
        return []


def test_count_sql_uses_active_statuses_and_task_type_id() -> None:
    sql = _COUNT_ACTIVE_BY_TYPE_SQL.lower()
    assert "task_type_id = $1" in sql
    for status in ACTIVE_STATUSES:
        assert status in _COUNT_ACTIVE_BY_TYPE_SQL


@pytest.mark.asyncio
async def test_count_active_tasks_queries_pg(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=7)

    @asynccontextmanager
    async def fake_acquire():
        yield conn

    monkeypatch.setattr("app_balance.queue.producers.base.acquire", fake_acquire)

    count = await count_active_tasks(3)
    assert count == 7
    conn.fetchval.assert_awaited_once_with(_COUNT_ACTIVE_BY_TYPE_SQL, 3)


@pytest.mark.asyncio
async def test_remaining_capacity_returns_none_when_unlimited() -> None:
    producer = _StubProducer()
    capacity = await producer.remaining_capacity(_task_type(target_queue_size=None))
    assert capacity is None


@pytest.mark.asyncio
async def test_remaining_capacity_subtracts_active_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=12),
    )
    producer = _StubProducer()
    capacity = await producer.remaining_capacity(_task_type(target_queue_size=20))
    assert capacity == 8


@pytest.mark.asyncio
async def test_remaining_capacity_never_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=25),
    )
    producer = _StubProducer()
    capacity = await producer.remaining_capacity(_task_type(target_queue_size=20))
    assert capacity == 0


@pytest.mark.asyncio
async def test_enqueue_if_room_creates_when_capacity_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_type = _task_type(target_queue_size=20)
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=task_type)

    task_queue = AsyncMock()
    task_queue.enqueue = AsyncMock(
        return_value=EnqueueResult(created=True, task_id=101)
    )

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=5),
    )

    producer = _StubProducer(task_queue=task_queue, task_types=task_types)
    data = EnqueueInput(
        task_type_code="move_channel",
        dedup_key="move:1:2",
        created_by="channel_balancer",
    )
    result = await producer.enqueue_if_room(data)

    assert result == ProduceResult(
        created=True,
        task_id=101,
        existing_task_id=None,
        skipped_reason=None,
    )
    task_queue.enqueue.assert_awaited_once_with(data)


@pytest.mark.asyncio
async def test_enqueue_if_room_skips_when_queue_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_type = _task_type(target_queue_size=20)
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=task_type)

    task_queue = AsyncMock()
    task_queue.enqueue = AsyncMock()

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=20),
    )

    producer = _StubProducer(task_queue=task_queue, task_types=task_types)
    data = EnqueueInput(task_type_code="move_channel", dedup_key="move:1:2")
    result = await producer.enqueue_if_room(data)

    assert result == ProduceResult(
        created=False,
        task_id=None,
        existing_task_id=None,
        skipped_reason="queue_full",
    )
    task_queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_if_room_skips_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_type = _task_type(target_queue_size=20)
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=task_type)

    task_queue = AsyncMock()
    task_queue.enqueue = AsyncMock(
        return_value=EnqueueResult(
            created=False,
            task_id=None,
            existing_task_id=55,
        )
    )

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=5),
    )

    producer = _StubProducer(task_queue=task_queue, task_types=task_types)
    data = EnqueueInput(task_type_code="move_channel", dedup_key="move:1:2")
    result = await producer.enqueue_if_room(data)

    assert result == ProduceResult(
        created=False,
        task_id=None,
        existing_task_id=55,
        skipped_reason="duplicate",
    )


@pytest.mark.asyncio
async def test_enqueue_if_room_no_limit_when_target_queue_size_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_type = _task_type(target_queue_size=None)
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=task_type)

    task_queue = AsyncMock()
    task_queue.enqueue = AsyncMock(
        return_value=EnqueueResult(created=True, task_id=200)
    )

    count_mock = AsyncMock(return_value=999)
    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        count_mock,
    )

    producer = _StubProducer(task_queue=task_queue, task_types=task_types)
    data = EnqueueInput(task_type_code="move_channel", dedup_key="move:9:10")
    result = await producer.enqueue_if_room(data)

    assert result.created is True
    assert result.task_id == 200
    count_mock.assert_not_awaited()
    task_queue.enqueue.assert_awaited_once_with(data)


@pytest.mark.asyncio
async def test_enqueue_if_room_raises_unknown_task_type() -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=None)

    producer = _StubProducer(task_types=task_types)
    data = EnqueueInput(task_type_code="unknown_type")

    with pytest.raises(UnknownTaskTypeError):
        await producer.enqueue_if_room(data)


@pytest.mark.asyncio
async def test_enqueue_if_room_raises_when_type_disabled() -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(
        return_value=_task_type(is_enabled=False)
    )

    producer = _StubProducer(task_types=task_types)
    data = EnqueueInput(task_type_code="move_channel")

    with pytest.raises(UnknownTaskTypeError):
        await producer.enqueue_if_room(data)
