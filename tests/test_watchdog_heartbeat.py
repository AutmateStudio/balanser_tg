"""Unit-тесты WatchdogRegistry / stale."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app_balance.queue.monitoring.watchdog_heartbeat import (
    WATCHDOG_STUCK,
    WatchdogHeartbeat,
    WatchdogRegistry,
)


def test_stale_when_never_ticked() -> None:
    hb = WatchdogHeartbeat(name=WATCHDOG_STUCK, enabled=True, interval_seconds=30)
    assert hb.is_stale() is True


def test_stale_when_older_than_two_intervals() -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    hb = WatchdogHeartbeat(
        name=WATCHDOG_STUCK,
        enabled=True,
        interval_seconds=30,
        last_tick_at=now - timedelta(seconds=61),
    )
    assert hb.is_stale(now=now) is True


def test_not_stale_within_two_intervals() -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    hb = WatchdogHeartbeat(
        name=WATCHDOG_STUCK,
        enabled=True,
        interval_seconds=30,
        last_tick_at=now - timedelta(seconds=50),
    )
    assert hb.is_stale(now=now) is False


@pytest.mark.asyncio
async def test_record_tick_updates_memory_without_pg() -> None:
    registry = WatchdogRegistry()
    await registry.record_tick(
        WATCHDOG_STUCK,
        duration_ms=5,
        result={"marked": 1},
        interval_seconds=30,
        enabled=True,
        process="test",
        persist=False,
    )
    items = {row["name"]: row for row in registry.list_status()}
    assert items[WATCHDOG_STUCK]["last_duration_ms"] == 5
    assert items[WATCHDOG_STUCK]["last_result"] == {"marked": 1}
    assert items[WATCHDOG_STUCK]["stale"] is False
    assert items[WATCHDOG_STUCK]["last_tick_at"] is not None
