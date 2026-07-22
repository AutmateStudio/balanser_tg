"""F8 — unit-тесты планировщика продюсеров (без PostgreSQL)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app_balance import queue_scheduler
from app_balance.queue.producers.base import ProduceResult
from app_balance.queue.producers.channel_balancer import ChannelBalancerProducer
from app_balance.queue.producers.collect_extra_data import CollectExtraDataProducer
from app_balance.queue.producers.update_channel import UpdateChannelProducer


def test_producers_registry_has_three_jobs() -> None:
    assert set(queue_scheduler.PRODUCERS) == {"collect", "update", "balancer"}
    assert queue_scheduler.PRODUCERS["collect"] is CollectExtraDataProducer
    assert queue_scheduler.PRODUCERS["update"] is UpdateChannelProducer
    assert queue_scheduler.PRODUCERS["balancer"] is ChannelBalancerProducer


def test_resolve_interval_cli_wins() -> None:
    assert queue_scheduler._resolve_interval(15.0) == 15.0


def test_resolve_interval_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRODUCER_INTERVAL_SECONDS", "30")
    assert queue_scheduler._resolve_interval(None) == 30.0


def test_resolve_interval_default_on_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRODUCER_INTERVAL_SECONDS", "abc")
    assert queue_scheduler._resolve_interval(None) == queue_scheduler.DEFAULT_INTERVAL_SECONDS


def test_resolve_interval_default_on_nonpositive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRODUCER_INTERVAL_SECONDS", "0")
    assert (
        queue_scheduler._resolve_interval(None)
        == queue_scheduler.DEFAULT_INTERVAL_SECONDS
    )


def test_summarize_counts_outcomes() -> None:
    results = [
        ProduceResult(created=True, task_id=1),
        ProduceResult(created=False, task_id=None, skipped_reason="duplicate"),
        ProduceResult(created=False, task_id=None, skipped_reason="queue_full"),
    ]
    summary = queue_scheduler._summarize(results)
    assert "создано=1" in summary
    assert "дубликатов=1" in summary
    assert "queue_full=1" in summary
    assert "всего=3" in summary


@pytest.mark.asyncio
async def test_run_tick_returns_results() -> None:
    producer = AsyncMock()
    producer.produce = AsyncMock(
        return_value=[ProduceResult(created=True, task_id=1)]
    )
    results = await queue_scheduler.run_tick(producer)
    assert len(results) == 1
    producer.produce.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_tick_swallows_errors() -> None:
    producer = AsyncMock()
    producer.produce = AsyncMock(side_effect=RuntimeError("boom"))
    results = await queue_scheduler.run_tick(producer)
    assert results == []


@pytest.mark.asyncio
async def test_run_loop_once_runs_single_tick() -> None:
    producer = AsyncMock()
    producer.produce = AsyncMock(return_value=[])
    stop = asyncio.Event()
    await queue_scheduler.run_loop(producer, 999.0, stop, once=True)
    producer.produce.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_loop_stops_on_event() -> None:
    producer = AsyncMock()
    calls = 0

    async def produce() -> list[ProduceResult]:
        nonlocal calls
        calls += 1
        if calls >= 3:
            stop.set()
        return []

    producer.produce = produce
    stop = asyncio.Event()
    await asyncio.wait_for(
        queue_scheduler.run_loop(producer, 0.0, stop, once=False), timeout=2.0
    )
    assert calls == 3


@pytest.mark.asyncio
async def test_serve_restores_clumps_for_balancer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init = AsyncMock()
    close = AsyncMock()
    monkeypatch.setattr(queue_scheduler.db, "init_pool", init)
    monkeypatch.setattr(queue_scheduler.db, "close_pool", close)

    fake_producer = AsyncMock()
    fake_producer.produce = AsyncMock(return_value=[])
    monkeypatch.setitem(
        queue_scheduler.PRODUCERS, "balancer", lambda: fake_producer
    )

    restore = AsyncMock()
    await queue_scheduler.serve(
        "balancer", 1.0, once=True, restore_clumps=restore
    )

    init.assert_awaited_once()
    close.assert_awaited_once()
    restore.assert_awaited_once()
    fake_producer.produce.assert_awaited_once()


@pytest.mark.asyncio
async def test_serve_skips_clumps_for_collect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(queue_scheduler.db, "init_pool", AsyncMock())
    monkeypatch.setattr(queue_scheduler.db, "close_pool", AsyncMock())

    fake_producer = AsyncMock()
    fake_producer.produce = AsyncMock(return_value=[])
    monkeypatch.setitem(
        queue_scheduler.PRODUCERS, "collect", lambda: fake_producer
    )

    restore = AsyncMock()
    await queue_scheduler.serve(
        "collect", 1.0, once=True, restore_clumps=restore
    )
    restore.assert_not_awaited()


def test_parser_requires_known_producer() -> None:
    parser = queue_scheduler._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["unknown"])


def test_parser_parses_flags() -> None:
    parser = queue_scheduler._build_parser()
    args = parser.parse_args(["update", "--once", "--interval", "42"])
    assert args.producer == "update"
    assert args.once is True
    assert args.interval == 42.0


def test_resolve_interval_default_when_no_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PRODUCER_INTERVAL_SECONDS", raising=False)
    assert (
        queue_scheduler._resolve_interval(None)
        == queue_scheduler.DEFAULT_INTERVAL_SECONDS
    )


def test_summarize_empty_results() -> None:
    summary = queue_scheduler._summarize([])
    assert "создано=0" in summary
    assert "всего=0" in summary


@pytest.mark.asyncio
async def test_serve_closes_pool_when_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init = AsyncMock()
    close = AsyncMock()
    monkeypatch.setattr(queue_scheduler.db, "init_pool", init)
    monkeypatch.setattr(queue_scheduler.db, "close_pool", close)

    fake_producer = AsyncMock()
    fake_producer.produce = AsyncMock(return_value=[])
    monkeypatch.setitem(
        queue_scheduler.PRODUCERS, "balancer", lambda: fake_producer
    )

    restore = AsyncMock(side_effect=RuntimeError("clump store down"))

    with pytest.raises(RuntimeError):
        await queue_scheduler.serve(
            "balancer", 1.0, once=True, restore_clumps=restore
        )

    init.assert_awaited_once()
    close.assert_awaited_once()
    fake_producer.produce.assert_not_called()


@pytest.mark.asyncio
async def test_run_loop_sleeps_interval_between_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = AsyncMock()
    producer.produce = AsyncMock(return_value=[])
    stop = asyncio.Event()

    sleeps: list[float] = []
    original_wait_for = asyncio.wait_for

    async def fake_wait_for(awaitable, timeout):
        sleeps.append(timeout)
        if len(sleeps) >= 2:
            stop.set()
        # Не зависаем на stop.wait() — гасим корутину и эмулируем таймаут.
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(queue_scheduler.asyncio, "wait_for", fake_wait_for)
    try:
        await queue_scheduler.run_loop(producer, 12.5, stop, once=False)
    finally:
        monkeypatch.setattr(queue_scheduler.asyncio, "wait_for", original_wait_for)

    assert sleeps == [12.5, 12.5]


def test_main_invokes_serve_with_parsed_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_serve(producer_name, interval_seconds, *, once):
        captured["producer"] = producer_name
        captured["interval"] = interval_seconds
        captured["once"] = once

    def fake_run(coro):
        # serve(...) возвращает корутину — выполняем её синхронно для проверки.
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(queue_scheduler, "serve", fake_serve)
    monkeypatch.setattr(queue_scheduler.asyncio, "run", fake_run)
    monkeypatch.delenv("PRODUCER_INTERVAL_SECONDS", raising=False)

    queue_scheduler.main(["collect", "--interval", "7"])

    assert captured == {"producer": "collect", "interval": 7.0, "once": False}
