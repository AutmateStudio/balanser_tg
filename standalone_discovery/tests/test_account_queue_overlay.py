"""Тесты overlay PG queue state на строки аккаунтов."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app_balance.queue.accounts import AccountQueueState
from discovery_api.queue.account_queue_overlay import overlay_queue_state


def _utc(y, m, d, h=0, mi=0, s=0) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


def test_overlay_queue_state_with_pg_cooldown() -> None:
    now = _utc(2026, 6, 30, 12, 0, 0)
    until = now + timedelta(seconds=270)
    pg = AccountQueueState(
        id=1,
        session_name="Client1",
        status="cooldown",
        is_enabled=True,
        cooldown_until=until,
        current_task_id=None,
        last_error="flood_wait",
        last_error_at=now,
    )
    row = overlay_queue_state(
        {"session_name": "Client1", "flood_until": None},
        pg,
        now=now,
    )
    assert row["queue_status"] == "cooldown"
    assert row["cooldown_remaining_seconds"] == 270
    assert row["cooldown_until"] == until.isoformat().replace("+00:00", "Z")
    assert row["available_in_seconds"] == 270
    assert row["last_error"] == "flood_wait"
    assert row["is_enabled"] is True


def test_overlay_queue_state_runtime_flood_only() -> None:
    now = _utc(2026, 6, 30, 12, 0, 0)
    flood_until = (now + timedelta(seconds=120)).timestamp()
    row = overlay_queue_state(
        {"session_name": "Test2", "flood_until": flood_until},
        None,
        now=now,
    )
    assert row["queue_status"] is None
    assert row["cooldown_until"] is None
    assert row["available_in_seconds"] == 120
    assert row["flood_until"] == flood_until


def test_overlay_pg_and_runtime_takes_max() -> None:
    now = _utc(2026, 6, 30, 12, 0, 0)
    pg_until = now + timedelta(seconds=100)
    flood_until = (now + timedelta(seconds=300)).timestamp()
    pg = AccountQueueState(
        id=2,
        session_name="Test3",
        status="cooldown",
        is_enabled=True,
        cooldown_until=pg_until,
        current_task_id=None,
        last_error=None,
        last_error_at=None,
    )
    row = overlay_queue_state(
        {"session_name": "Test3", "flood_until": flood_until},
        pg,
        now=now,
    )
    assert row["available_in_seconds"] == 300
