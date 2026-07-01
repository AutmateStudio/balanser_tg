"""F2 — unit-тесты ChannelBalancerProducer (без PostgreSQL)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app_balance.queue.per_op_reading import TaskType
from app_balance.queue.producers.channel_balancer import (
    ChannelBalancerProducer,
    _dedup_key,
)
from app_balance.queue.source_channels import ChannelRef
from app_balance.queue.task_queue import EnqueueResult


def _task_type(target_queue_size: int | None = 20) -> TaskType:
    return TaskType(
        id=3,
        code="move_channel",
        name="move_channel",
        description=None,
        is_enabled=True,
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


class _FakeTaskTypes:
    def __init__(self, task_type: TaskType) -> None:
        self._task_type = task_type

    async def get_by_code(self, code: str) -> TaskType | None:
        return self._task_type


class _FakeTaskQueue:
    """Фиксирует enqueue и выдаёт последовательные task_id."""

    def __init__(self) -> None:
        self.calls: list = []
        self._next_id = 100

    async def enqueue(self, data) -> EnqueueResult:
        self.calls.append(data)
        self._next_id += 1
        return EnqueueResult(created=True, task_id=self._next_id)


class _FakeTaskQueueFirstFatal(_FakeTaskQueue):
    """Первый enqueue — fatal_history, остальные — успешны (F1 skip-reason)."""

    async def enqueue(self, data) -> EnqueueResult:
        self.calls.append(data)
        if len(self.calls) == 1:
            return EnqueueResult(
                created=False,
                task_id=None,
                existing_task_id=555,
                skipped_reason="fatal_history",
                fatal_error_code="banned",
            )
        self._next_id += 1
        return EnqueueResult(created=True, task_id=self._next_id)


class _FakeAccounts:
    """session_name -> account_id (None => аккаунт не в PG)."""

    def __init__(self, mapping: dict[str, int | None]) -> None:
        self._mapping = mapping

    async def get_id_by_session_name(self, session_name: str) -> int | None:
        return self._mapping.get(session_name)


class _FakeChannels:
    def __init__(
        self,
        counts: dict[int, int],
        channels: dict[int, list[ChannelRef]] | None = None,
    ) -> None:
        self._counts = counts
        self._channels = channels or {}

    async def count_channels_by_accounts(
        self, account_ids: list[int]
    ) -> dict[int, int]:
        return {aid: self._counts[aid] for aid in account_ids if aid in self._counts}

    async def list_channels_for_account(
        self, account_id: int, limit: int
    ) -> list[ChannelRef]:
        return list(self._channels.get(account_id, []))


class _PC:
    def __init__(self, session_name: str) -> None:
        self.session_name = session_name


class _Clump:
    def __init__(self, sessions: list[str]) -> None:
        self.parser_client_list = [_PC(s) for s in sessions]


def _channels_for(account_id: int, n: int, *, url: bool = True) -> list[ChannelRef]:
    out = []
    for i in range(n):
        cid = account_id * 1000 + i
        out.append(
            ChannelRef(
                id=cid,
                external_url=f"https://t.me/c{cid}" if url else None,
                external_channel_id=f"ext{cid}",
            )
        )
    return out


def _producer(
    *,
    counts: dict[int, int],
    accounts: dict[str, int | None],
    clumps: list[tuple[str, _Clump]],
    channels: dict[int, list[ChannelRef]] | None = None,
    task_queue: _FakeTaskQueue | None = None,
    target_queue_size: int | None = 20,
) -> tuple[ChannelBalancerProducer, _FakeTaskQueue]:
    tq = task_queue or _FakeTaskQueue()
    producer = ChannelBalancerProducer(
        task_queue=tq,  # type: ignore[arg-type]
        task_types=_FakeTaskTypes(_task_type(target_queue_size)),  # type: ignore[arg-type]
        accounts=_FakeAccounts(accounts),  # type: ignore[arg-type]
        channels=_FakeChannels(counts, channels),  # type: ignore[arg-type]
        clumps_provider=lambda: clumps,
    )
    return producer, tq


@pytest.fixture(autouse=True)
def _patch_count_active(monkeypatch: pytest.MonkeyPatch):
    """По умолчанию активных задач move_channel нет (очередь не заполнена)."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=0),
    )


def test_dedup_key_format() -> None:
    assert _dedup_key(555, 1, 2) == "move_channel:555:1:2"


@pytest.mark.asyncio
async def test_balanced_clump_creates_nothing() -> None:
    producer, tq = _producer(
        counts={1: 10, 2: 10},
        accounts={"s1": 1, "s2": 2},
        clumps=[("p1", _Clump(["s1", "s2"]))],
        channels={1: _channels_for(1, 10)},
    )
    results = await producer.produce()
    assert results == []
    assert tq.calls == []


@pytest.mark.asyncio
async def test_within_threshold_creates_nothing() -> None:
    # avg=20.5, high=21.5, low=19.5 → 21 и 20 в пределах ±5%.
    producer, tq = _producer(
        counts={1: 21, 2: 20},
        accounts={"s1": 1, "s2": 2},
        clumps=[("p1", _Clump(["s1", "s2"]))],
        channels={1: _channels_for(1, 21)},
    )
    results = await producer.produce()
    assert tq.calls == []


@pytest.mark.asyncio
async def test_skew_creates_move_until_balanced() -> None:
    # avg=10, high=10.5, low=9.5 → 12 и 8 выравниваются за 2 переноса.
    producer, tq = _producer(
        counts={1: 12, 2: 8},
        accounts={"s1": 1, "s2": 2},
        clumps=[("p1", _Clump(["s1", "s2"]))],
        channels={1: _channels_for(1, 12)},
    )
    results = await producer.produce()
    created = [r for r in results if r.created]
    assert len(created) == 2
    for data in tq.calls:
        assert data.task_type_code == "move_channel"
        assert data.source_account_id == 1
        assert data.target_account_id == 2
        assert data.channel_id is not None
        assert data.payload["parser_id"] == "p1"


@pytest.mark.asyncio
async def test_dedup_key_and_channel_ref_external_url() -> None:
    producer, tq = _producer(
        counts={1: 12, 2: 8},
        accounts={"s1": 1, "s2": 2},
        clumps=[("p1", _Clump(["s1", "s2"]))],
        channels={1: _channels_for(1, 12)},
    )
    await producer.produce()
    first = tq.calls[0]
    assert first.dedup_key == _dedup_key(first.channel_id, 1, 2)
    assert first.payload["channel_ref"].startswith("https://t.me/")


@pytest.mark.asyncio
async def test_channel_ref_fallback_to_external_id() -> None:
    producer, tq = _producer(
        counts={1: 12, 2: 8},
        accounts={"s1": 1, "s2": 2},
        clumps=[("p1", _Clump(["s1", "s2"]))],
        channels={1: _channels_for(1, 12, url=False)},
    )
    await producer.produce()
    first = tq.calls[0]
    assert first.payload["channel_ref"].startswith("ext")


@pytest.mark.asyncio
async def test_queue_full_stops_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=20),
    )
    producer, tq = _producer(
        counts={1: 12, 2: 8},
        accounts={"s1": 1, "s2": 2},
        clumps=[("p1", _Clump(["s1", "s2"]))],
        channels={1: _channels_for(1, 12)},
        target_queue_size=20,
    )
    results = await producer.produce()
    assert tq.calls == []
    assert any(r.skipped_reason == "queue_full" for r in results)


@pytest.mark.asyncio
async def test_unknown_session_skipped_and_single_account_clump_noop() -> None:
    # s2 не в PG → остаётся 1 аккаунт → clump пропускается.
    producer, tq = _producer(
        counts={1: 12},
        accounts={"s1": 1, "s2": None},
        clumps=[("p1", _Clump(["s1", "s2"]))],
        channels={1: _channels_for(1, 12)},
    )
    results = await producer.produce()
    assert results == []
    assert tq.calls == []


@pytest.mark.asyncio
async def test_fatal_history_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tq = _FakeTaskQueueFirstFatal()
    producer, tq = _producer(
        counts={1: 12, 2: 8},
        accounts={"s1": 1, "s2": 2},
        clumps=[("p1", _Clump(["s1", "s2"]))],
        channels={1: _channels_for(1, 12)},
        task_queue=tq,
    )
    with caplog.at_level(
        "WARNING", logger="app_balance.queue.producers.channel_balancer"
    ):
        results = await producer.produce()

    assert any(r.skipped_reason == "fatal_history" for r in results)
    assert any(
        "move_channel" in record.message and "фатально" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_no_channels_available_breaks_loop() -> None:
    # Перекос есть, но у source нет каналов для переноса → задач нет.
    producer, tq = _producer(
        counts={1: 12, 2: 8},
        accounts={"s1": 1, "s2": 2},
        clumps=[("p1", _Clump(["s1", "s2"]))],
        channels={1: []},
    )
    results = await producer.produce()
    assert tq.calls == []
