"""G7★ — unit/integration тесты порогов загрузки каналов и ресурса."""
from __future__ import annotations

import math
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app_balance.queue import db
from app_balance.queue.monitoring.config import AlertConfig
from app_balance.queue.monitoring.metrics_repo import (
    AccountResourceRow,
    AccountsMetrics,
    AlertsPreview,
    ChannelCapacityMetrics,
    MetricsSnapshot,
    QueueMetrics,
)
from app_balance.queue.monitoring.notify import AlertNotifier
from app_balance.queue.monitoring.threshold_rules import evaluate_threshold_alerts
from app_balance.queue.monitoring.alert_rules import Alert
from app_balance.queue.monitoring.queue_growth import QueueGrowthTracker
from app_balance.queue.monitoring.metrics_repo import MetricsRepo
from app_balance.queue_monitor import run_tick
from tests.conftest import requires_pg


def _config(**overrides) -> AlertConfig:
    base = AlertConfig(
        enabled=True,
        cooldown_seconds=1800,
        threshold_enabled=True,
        threshold_channel_percent=75.0,
        threshold_resource_percent=0.0,
        max_channels_per_session=10,
        telegram_chat_id="-100123",
        bot_token="test-bot-token",
        webhook_url="https://example.test/hook",
    )
    return replace(base, **overrides)


def _snapshot(
    *,
    active: int = 1,
    assigned_channels: int = 0,
    usage_percent: float | None = None,
    max_channels_per_session: int = 10,
    worst_by_account: tuple[AccountResourceRow, ...] = (),
) -> MetricsSnapshot:
    fleet_capacity = active * max_channels_per_session
    if usage_percent is None:
        usage_percent = (
            (assigned_channels / fleet_capacity * 100.0) if fleet_capacity > 0 else 0.0
        )
    return MetricsSnapshot(
        queue=QueueMetrics(
            total=0,
            by_status={},
            by_type={},
            oldest_queued_age_seconds=0,
            stuck_count=0,
            done_last_5_min=0,
        ),
        accounts=AccountsMetrics(
            active=active,
            in_cooldown=0,
            without_resource=0,
            worst_by_account=worst_by_account,
        ),
        alerts_preview=AlertsPreview(high_postpone_count=0),
        channels=ChannelCapacityMetrics(
            active_accounts=active,
            assigned_channels_total=assigned_channels,
            fleet_capacity=fleet_capacity,
            usage_percent=usage_percent,
        ),
        generated_at=datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc),
    )


def _codes(alerts: list[Alert]) -> set[str]:
    return {a.code for a in alerts}


def test_channel_threshold_not_fired_below_75() -> None:
    config = _config()
    alerts = evaluate_threshold_alerts(
        _snapshot(active=1, assigned_channels=7, usage_percent=70.0),
        config,
    )
    assert "threshold_channel_capacity" not in _codes(alerts)


def test_channel_threshold_fired_at_75() -> None:
    config = _config()
    alerts = evaluate_threshold_alerts(
        _snapshot(active=1, assigned_channels=8, usage_percent=80.0),
        config,
    )
    assert "threshold_channel_capacity" in _codes(alerts)
    ch = next(a for a in alerts if a.code == "threshold_channel_capacity")
    assert ch.scope_key == "global"
    assert ch.severity == "WARNING"
    assert "80.0%" in ch.message


def test_channel_threshold_skipped_when_fleet_capacity_zero() -> None:
    config = _config()
    snap = _snapshot(active=0, assigned_channels=0, usage_percent=100.0)
    alerts = evaluate_threshold_alerts(snap, config)
    assert "threshold_channel_capacity" not in _codes(alerts)


def test_resource_exhausted_for_active_account() -> None:
    config = _config()
    worst = (
        AccountResourceRow(
            account_id=42,
            session_name="acc_test",
            account_status="active",
            worst_available_percent=0.0,
            any_op_exhausted=True,
            exhausted_ops_count=1,
        ),
    )
    alerts = evaluate_threshold_alerts(_snapshot(worst_by_account=worst), config)
    assert "threshold_resource_exhausted" in _codes(alerts)
    res = next(a for a in alerts if a.code == "threshold_resource_exhausted")
    assert res.scope_key == "account:42"
    assert res.severity == "ERROR"


def test_resource_not_alerted_for_inactive_account() -> None:
    config = _config()
    worst = (
        AccountResourceRow(
            account_id=99,
            session_name="acc_cooldown",
            account_status="cooldown",
            worst_available_percent=0.0,
            any_op_exhausted=True,
            exhausted_ops_count=1,
        ),
    )
    alerts = evaluate_threshold_alerts(_snapshot(worst_by_account=worst), config)
    assert "threshold_resource_exhausted" not in _codes(alerts)


def test_threshold_disabled_returns_empty() -> None:
    config = _config(threshold_enabled=False)
    alerts = evaluate_threshold_alerts(
        _snapshot(active=1, usage_percent=100.0),
        config,
    )
    assert alerts == []


@pytest.mark.asyncio
async def test_threshold_notifier_debounce() -> None:
    config = _config(cooldown_seconds=1800)
    notifier = AlertNotifier(config)
    alert = Alert(
        code="threshold_channel_capacity",
        severity="WARNING",
        message="test",
        scope_key="global",
        metrics_snapshot={},
    )
    with patch(
        "app_balance.queue.monitoring.notify.send_telegram_dev",
        new_callable=AsyncMock,
    ) as mock_tg:
        assert await notifier.emit(alert) is True
        assert await notifier.emit(alert) is False
        mock_tg.assert_awaited_once()


@pytest.mark.asyncio
async def test_threshold_alert_sends_telegram_not_g4_webhook_only() -> None:
    config = _config()
    notifier = AlertNotifier(config)
    threshold = Alert(
        code="threshold_channel_capacity",
        severity="WARNING",
        message="каналы 80%",
        scope_key="global",
        metrics_snapshot={},
    )
    g4 = Alert(
        code="queue_growth",
        severity="WARNING",
        message="рост",
        scope_key="global",
        metrics_snapshot={},
    )
    with patch(
        "app_balance.queue.monitoring.notify.send_telegram_dev",
        new_callable=AsyncMock,
    ) as mock_tg, patch.object(
        notifier, "_post_webhook", new_callable=AsyncMock
    ) as mock_wh:
        await notifier.emit(threshold)
        await notifier.emit(g4)
        mock_tg.assert_awaited_once_with(
            "каналы 80%",
            chat_id="-100123",
            bot_token="test-bot-token",
        )
        assert mock_wh.await_count == 2


@pytest.mark.asyncio
@requires_pg
async def test_integration_channel_threshold_with_view(pg_pool) -> None:
    """INSERT каналов → snapshot → правило при низком max_channels_per_session."""
    suffix = uuid.uuid4().hex
    session_name = f"test_g7_{suffix}"
    platform_code = f"test_g7_plat_{suffix}"

    config = _config(max_channels_per_session=10, threshold_channel_percent=75.0)

    async with db.acquire() as conn:
        capacity_before = await conn.fetchrow("SELECT * FROM v_channel_capacity_usage")
        active_before = int(capacity_before["active_accounts_count"])
        assigned_before = int(capacity_before["assigned_channels_total"])
        # +1 active — тестовый аккаунт; fleet и порог считаются глобально (shared PG).
        fleet_after = (active_before + 1) * config.max_channels_per_session
        min_assigned = math.ceil(
            fleet_after * config.threshold_channel_percent / 100.0
        )
        n_channels = max(8, min_assigned - assigned_before + 1)

        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )
        platform_id = await conn.fetchval(
            "INSERT INTO platforms (code, name) VALUES ($1, $2) RETURNING id",
            platform_code,
            "G7 integration",
        )
        for i in range(n_channels):
            await conn.execute(
                """
                INSERT INTO source_channels (
                    platform_id, external_channel_id, name,
                    assigned_account_id, is_active
                ) VALUES ($1, $2, $3, $4, true)
                """,
                platform_id,
                f"test_g7_ch_{suffix}_{i}",
                f"ch {i}",
                account_id,
            )

    try:
        snapshot, _ctx = await MetricsRepo().fetch_alert_context(config)
        alerts = evaluate_threshold_alerts(snapshot, config)
        channel_codes = {a.code for a in alerts if a.code == "threshold_channel_capacity"}
        assert "threshold_channel_capacity" in channel_codes
        assert snapshot.channels.usage_percent >= config.threshold_channel_percent
        assert snapshot.channels.assigned_channels_total >= assigned_before + n_channels
    finally:
        async with db.acquire() as conn:
            await conn.execute(
                "DELETE FROM source_channels WHERE external_channel_id LIKE $1",
                f"test_g7_ch_{suffix}_%",
            )
            await conn.execute("DELETE FROM platforms WHERE id = $1", platform_id)
            await conn.execute("DELETE FROM accounts WHERE id = $1", account_id)


@pytest.mark.asyncio
@requires_pg
async def test_integration_run_tick_emits_threshold_telegram(pg_pool) -> None:
    suffix = uuid.uuid4().hex
    session_name = f"test_g7_tick_{suffix}"

    async with db.acquire() as conn:
        await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )

    config = _config(threshold_channel_percent=0.0, max_channels_per_session=500)
    repo = MetricsRepo()
    growth = QueueGrowthTracker(window_seconds=900)
    notifier = AlertNotifier(config)

    with patch(
        "app_balance.queue.monitoring.notify.send_telegram_dev",
        new_callable=AsyncMock,
    ) as mock_tg:
        emitted = await run_tick(repo, config, growth, notifier)
        assert emitted >= 1
        mock_tg.assert_awaited()

    async with db.acquire() as conn:
        await conn.execute("DELETE FROM accounts WHERE session_name = $1", session_name)
