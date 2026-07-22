"""Unit-тесты compute_availability (PG cooldown + runtime flood)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app_balance.queue.account_availability import (
    compute_availability,
    cooldown_remaining_seconds,
)


def _utc(y, m, d, h=0, mi=0, s=0) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


def test_compute_availability_pg_only() -> None:
    now = _utc(2026, 6, 30, 12, 0, 0)
    until = now + timedelta(seconds=270)
    available_at, secs = compute_availability(
        now=now,
        cooldown_until=until,
        flood_until_unix=None,
    )
    assert available_at == until
    assert secs == 270


def test_compute_availability_runtime_only() -> None:
    now = _utc(2026, 6, 30, 12, 0, 0)
    flood_until = (now + timedelta(seconds=120)).timestamp()
    available_at, secs = compute_availability(
        now=now,
        cooldown_until=None,
        flood_until_unix=flood_until,
    )
    assert available_at is not None
    assert secs == 120


def test_compute_availability_takes_max_of_both() -> None:
    now = _utc(2026, 6, 30, 12, 0, 0)
    pg_until = now + timedelta(seconds=100)
    flood_until = (now + timedelta(seconds=300)).timestamp()
    available_at, secs = compute_availability(
        now=now,
        cooldown_until=pg_until,
        flood_until_unix=flood_until,
    )
    assert secs == 300
    assert available_at == datetime.fromtimestamp(flood_until, tz=timezone.utc)


def test_compute_availability_expired_returns_none() -> None:
    now = _utc(2026, 6, 30, 12, 0, 0)
    past = now - timedelta(seconds=60)
    available_at, secs = compute_availability(
        now=now,
        cooldown_until=past,
        flood_until_unix=past.timestamp(),
    )
    assert available_at is None
    assert secs is None


def test_cooldown_remaining_seconds_active() -> None:
    now = _utc(2026, 6, 30, 12, 0, 0)
    assert cooldown_remaining_seconds(now=now, cooldown_until=now + timedelta(seconds=90)) == 90


def test_cooldown_remaining_seconds_expired() -> None:
    now = _utc(2026, 6, 30, 12, 0, 0)
    assert cooldown_remaining_seconds(now=now, cooldown_until=now - timedelta(1)) is None
