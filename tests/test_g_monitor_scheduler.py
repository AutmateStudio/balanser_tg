"""G4/G6/G7 — unit-тесты планировщика queue_monitor (без PostgreSQL)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app_balance import queue_monitor
from app_balance.queue.monitoring.config import AlertConfig


def test_monitor_modes_in_parser_choices() -> None:
    parser = queue_monitor._build_parser()
    mode_action = next(
        a for a in parser._actions if getattr(a, "dest", None) == "mode"
    )
    assert set(mode_action.choices) == {"alerts", "detector", "all"}


def test_resolve_interval_cli_wins() -> None:
    cfg = AlertConfig(monitor_interval_seconds=120.0)
    assert queue_monitor._resolve_interval(15.0, cfg) == 15.0


def test_resolve_interval_from_config() -> None:
    cfg = AlertConfig(monitor_interval_seconds=90.0)
    assert queue_monitor._resolve_interval(None, cfg) == 90.0


@pytest.mark.asyncio
async def test_run_loop_once_exits_after_single_tick() -> None:
    stop = asyncio.Event()
    tick = AsyncMock(return_value=0)

    with patch.object(queue_monitor, "run_combined_tick", tick):
        await queue_monitor.run_loop(
            60.0,
            stop,
            once=True,
            mode="all",
            alert_config=AlertConfig(enabled=False),
            detector_config=MagicMock(enabled=False),
        )

    tick.assert_awaited_once()
    assert tick.await_args.args[0] == "all"


@pytest.mark.asyncio
async def test_run_loop_repeats_until_stop() -> None:
    stop = asyncio.Event()
    tick = AsyncMock(return_value=0)

    async def fake_wait_for(_awaitable, *, timeout):
        stop.set()
        raise asyncio.TimeoutError

    with patch.object(queue_monitor, "run_combined_tick", tick):
        with patch.object(queue_monitor.asyncio, "wait_for", fake_wait_for):
            await queue_monitor.run_loop(
                12.5,
                stop,
                once=False,
                mode="alerts",
                alert_config=AlertConfig(enabled=False),
                detector_config=MagicMock(enabled=False),
            )

    assert tick.await_count == 1


def test_main_invokes_serve_with_parsed_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_serve(interval_seconds, *, once, mode):
        captured["interval"] = interval_seconds
        captured["once"] = once
        captured["mode"] = mode

    def fake_run(coro):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(queue_monitor, "serve", fake_serve)
    monkeypatch.setattr(queue_monitor.asyncio, "run", fake_run)
    monkeypatch.setattr(
        queue_monitor.AlertConfig,
        "from_env",
        classmethod(lambda cls: AlertConfig(monitor_interval_seconds=120.0)),
    )

    queue_monitor.main(["all", "--once", "--interval", "7"])

    assert captured == {"interval": 7.0, "once": True, "mode": "all"}
