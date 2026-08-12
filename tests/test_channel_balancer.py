"""F2 — unit-тесты ChannelBalancerProducer (fleet-wide, без PostgreSQL)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from app_balance.queue.accounts import AccountQueueState
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
        default_priority=600,
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


@dataclass(frozen=True)
class _AcctSpec:
    """account_id + PG live/dead-классификация для одного session_name."""

    account_id: int
    status: str = "active"
    is_enabled: bool = True


def _acct(account_id: int, *, status: str = "active", enabled: bool = True) -> _AcctSpec:
    return _AcctSpec(account_id=account_id, status=status, is_enabled=enabled)


class _FakeAccounts:
    """session_name -> _AcctSpec (None => аккаунт не в PG)."""

    def __init__(self, mapping: dict[str, _AcctSpec | None]) -> None:
        self._mapping = mapping

    async def get_ids_by_session_names(self, session_names: list[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for name in session_names:
            spec = self._mapping.get(name)
            if spec is not None:
                out[name] = spec.account_id
        return out

    async def list_queue_states(self) -> dict[str, AccountQueueState]:
        out: dict[str, AccountQueueState] = {}
        for name, spec in self._mapping.items():
            if spec is None:
                continue
            out[name] = AccountQueueState(
                id=spec.account_id,
                session_name=name,
                status=spec.status,
                is_enabled=spec.is_enabled,
                cooldown_until=None,
                current_task_id=None,
                last_error=None,
                last_error_at=None,
            )
        return out


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
    accounts: dict[str, _AcctSpec | None],
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
async def test_balanced_fleet_creates_nothing() -> None:
    # Регресс на старый баг: 2 РАЗНЫХ clump (1 сессия каждый), не 1 clump с 2
    # сессиями — именно так выглядит текущая прод-топология (47 clump по 1).
    producer, tq = _producer(
        accounts={"s1": _acct(1), "s2": _acct(2)},
        counts={1: 10, 2: 10},
        clumps=[("p1", _Clump(["s1"])), ("p2", _Clump(["s2"]))],
        channels={1: _channels_for(1, 10)},
    )
    results = await producer.produce()
    assert results == []
    assert tq.calls == []


@pytest.mark.asyncio
async def test_within_threshold_creates_nothing() -> None:
    # avg=20.5, high=21.5, low=19.5 → 21 и 20 в пределах ±5%.
    producer, tq = _producer(
        accounts={"s1": _acct(1), "s2": _acct(2)},
        counts={1: 21, 2: 20},
        clumps=[("p1", _Clump(["s1"])), ("p2", _Clump(["s2"]))],
        channels={1: _channels_for(1, 21)},
    )
    results = await producer.produce()
    assert tq.calls == []


@pytest.mark.asyncio
async def test_skew_creates_move_until_balanced() -> None:
    # avg=10, high=10.5, low=9.5 → 12 и 8 выравниваются за 2 переноса.
    # Источник и цель — РАЗНЫЕ clump (p1/p2) — payload должен нести оба parser_id.
    producer, tq = _producer(
        accounts={"s1": _acct(1), "s2": _acct(2)},
        counts={1: 12, 2: 8},
        clumps=[("p1", _Clump(["s1"])), ("p2", _Clump(["s2"]))],
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
        assert data.payload["source_parser_id"] == "p1"
        assert data.payload["target_parser_id"] == "p2"


@pytest.mark.asyncio
async def test_dedup_key_and_channel_ref_external_url() -> None:
    producer, tq = _producer(
        accounts={"s1": _acct(1), "s2": _acct(2)},
        counts={1: 12, 2: 8},
        clumps=[("p1", _Clump(["s1"])), ("p2", _Clump(["s2"]))],
        channels={1: _channels_for(1, 12)},
    )
    await producer.produce()
    first = tq.calls[0]
    assert first.dedup_key == _dedup_key(first.channel_id, 1, 2)
    assert first.payload["channel_ref"].startswith("https://t.me/")


@pytest.mark.asyncio
async def test_channel_ref_fallback_to_external_id() -> None:
    producer, tq = _producer(
        accounts={"s1": _acct(1), "s2": _acct(2)},
        counts={1: 12, 2: 8},
        clumps=[("p1", _Clump(["s1"])), ("p2", _Clump(["s2"]))],
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
        accounts={"s1": _acct(1), "s2": _acct(2)},
        counts={1: 12, 2: 8},
        clumps=[("p1", _Clump(["s1"])), ("p2", _Clump(["s2"]))],
        channels={1: _channels_for(1, 12)},
        target_queue_size=20,
    )
    results = await producer.produce()
    assert tq.calls == []
    assert any(r.skipped_reason == "queue_full" for r in results)


@pytest.mark.asyncio
async def test_unknown_session_excluded_from_fleet() -> None:
    # s2 не в PG → исключён из фермы → остаётся только s1 (1 живой аккаунт) →
    # переносить некуда (нужен минимум 2 живых).
    producer, tq = _producer(
        accounts={"s1": _acct(1), "s2": None},
        counts={1: 12},
        clumps=[("p1", _Clump(["s1"])), ("p2", _Clump(["s2"]))],
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
        accounts={"s1": _acct(1), "s2": _acct(2)},
        counts={1: 12, 2: 8},
        clumps=[("p1", _Clump(["s1"])), ("p2", _Clump(["s2"]))],
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
        accounts={"s1": _acct(1), "s2": _acct(2)},
        counts={1: 12, 2: 8},
        clumps=[("p1", _Clump(["s1"])), ("p2", _Clump(["s2"]))],
        channels={1: []},
    )
    results = await producer.produce()
    assert tq.calls == []


@pytest.mark.asyncio
async def test_dead_account_drains_to_least_loaded_live_account() -> None:
    # s1 banned с 4 каналами, s2/s3 живые и изначально пусты — drain безусловен
    # (не зависит от ±5%-порога) и должен размазаться по обоим живым поровну.
    producer, tq = _producer(
        accounts={"s1": _acct(1, status="banned"), "s2": _acct(2), "s3": _acct(3)},
        counts={1: 4, 2: 0, 3: 0},
        clumps=[
            ("p1", _Clump(["s1"])),
            ("p2", _Clump(["s2"])),
            ("p3", _Clump(["s3"])),
        ],
        channels={1: _channels_for(1, 4)},
    )
    results = await producer.produce()
    created = [r for r in results if r.created]
    assert len(created) == 4
    assert all(data.source_account_id == 1 for data in tq.calls)
    targets = [data.target_account_id for data in tq.calls]
    assert set(targets) == {2, 3}
    assert sorted(targets) == [2, 2, 3, 3]  # поровну — сходится к честной доле


@pytest.mark.asyncio
async def test_dead_account_drains_even_when_live_fleet_already_balanced() -> None:
    # Живые s2/s3 УЖЕ идеально сбалансированы (по 5) — без мёртвого аккаунта
    # цикл skew сразу остановился бы. banned s1 должен всё равно дренироваться.
    producer, tq = _producer(
        accounts={"s1": _acct(1, status="banned"), "s2": _acct(2), "s3": _acct(3)},
        counts={1: 2, 2: 5, 3: 5},
        clumps=[
            ("p1", _Clump(["s1"])),
            ("p2", _Clump(["s2"])),
            ("p3", _Clump(["s3"])),
        ],
        channels={1: _channels_for(1, 2)},
    )
    results = await producer.produce()
    created = [r for r in results if r.created]
    assert len(created) == 2
    assert all(data.source_account_id == 1 for data in tq.calls)


@pytest.mark.asyncio
async def test_dead_account_prioritized_over_live_skew() -> None:
    # Мёртвый source (s1) и живой перекос (s2 >> s3) одновременно — drain
    # мёртвого должен идти ПЕРВЫМ (порядок enqueue-вызовов).
    producer, tq = _producer(
        accounts={"s1": _acct(1, status="banned"), "s2": _acct(2), "s3": _acct(3)},
        counts={1: 1, 2: 20, 3: 0},
        clumps=[
            ("p1", _Clump(["s1"])),
            ("p2", _Clump(["s2"])),
            ("p3", _Clump(["s3"])),
        ],
        channels={1: _channels_for(1, 1), 2: _channels_for(2, 20)},
    )
    results = await producer.produce()
    assert tq.calls  # хотя бы один enqueue произошёл
    assert tq.calls[0].source_account_id == 1


@pytest.mark.asyncio
async def test_disabled_live_status_account_treated_as_dead() -> None:
    # is_enabled=false — тоже "мёртвый" источник, даже если status=active.
    producer, tq = _producer(
        accounts={
            "s1": _acct(1, status="active", enabled=False),
            "s2": _acct(2),
        },
        counts={1: 4, 2: 0},
        clumps=[("p1", _Clump(["s1"])), ("p2", _Clump(["s2"]))],
        channels={1: _channels_for(1, 4)},
    )
    results = await producer.produce()
    created = [r for r in results if r.created]
    assert len(created) == 4
    assert all(data.source_account_id == 1 and data.target_account_id == 2 for data in tq.calls)


@pytest.mark.asyncio
async def test_all_dead_fleet_creates_nothing() -> None:
    # Вся ферма мертва — переносить некуда, безопасный no-op, не падаем.
    producer, tq = _producer(
        accounts={"s1": _acct(1, status="banned"), "s2": _acct(2, status="error")},
        counts={1: 4, 2: 4},
        clumps=[("p1", _Clump(["s1"])), ("p2", _Clump(["s2"]))],
        channels={1: _channels_for(1, 4)},
    )
    results = await producer.produce()
    assert results == []
    assert tq.calls == []


@pytest.mark.asyncio
async def test_session_in_two_clumps_logs_warning_and_keeps_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    producer, tq = _producer(
        accounts={"s1": _acct(1), "s2": _acct(2)},
        counts={1: 10, 2: 10},
        clumps=[("p1", _Clump(["s1"])), ("p2", _Clump(["s1", "s2"]))],
        channels={},
    )
    with caplog.at_level(
        "WARNING", logger="app_balance.queue.producers.channel_balancer"
    ):
        results = await producer.produce()
    assert results == []
    assert any("числится в двух clump" in record.message for record in caplog.records)
