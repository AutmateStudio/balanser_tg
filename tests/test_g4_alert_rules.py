"""G4 — unit/integration тесты правил алертов §26.4."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app_balance.queue import db
from app_balance.queue.monitoring.alert_rules import Alert, evaluate_alerts
from app_balance.queue.monitoring.config import AlertConfig
from app_balance.queue.monitoring.metrics_repo import (
    AccountResourceRow,
    AccountsMetrics,
    AlertContext,
    AlertsPreview,
    ChannelCapacityMetrics,
    ErrorRateRow,
    HighPostponeTaskRow,
    MetricsSnapshot,
    QueueMetrics,
)
from app_balance.queue.monitoring.notify import AlertNotifier
from app_balance.queue.monitoring.queue_growth import QueueGrowthTracker
from app_balance.queue_monitor import run_tick
from tests.conftest import requires_pg, TEST_ISOLATION_PRIORITY


def _default_config(**overrides) -> AlertConfig:
    base = AlertConfig(
        enabled=True,
        cooldown_seconds=1800,
        queue_growth_percent=20.0,
        queue_growth_window_seconds=900,
        oldest_queued_max_seconds=3600,
        high_postpone_min=10,
        error_rate_min_percent=50.0,
        error_rate_min_attempts=5,
        webhook_url="https://example.test/hook",
        monitor_interval_seconds=120.0,
    )
    return replace(base, **overrides)


def _snapshot(
    *,
    total: int = 0,
    oldest_age: int = 0,
    stuck: int = 0,
    done_5min: int = 0,
    active: int = 1,
    assigned_channels: int = 0,
    usage_percent: float | None = None,
    max_channels_per_session: int = 500,
    worst_by_account: tuple[AccountResourceRow, ...] = (),
) -> MetricsSnapshot:
    fleet_capacity = active * max_channels_per_session
    if usage_percent is None:
        usage_percent = (
            (assigned_channels / fleet_capacity * 100.0) if fleet_capacity > 0 else 0.0
        )
    return MetricsSnapshot(
        queue=QueueMetrics(
            total=total,
            by_status={},
            by_type={},
            oldest_queued_age_seconds=oldest_age,
            stuck_count=stuck,
            done_last_5_min=done_5min,
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


def _empty_ctx() -> AlertContext:
    return AlertContext()


def _codes(alerts) -> set[str]:
    return {a.code for a in alerts}


def test_queue_growth_alert_when_above_threshold() -> None:
    config = _default_config()
    growth = QueueGrowthTracker(window_seconds=900)
    now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
    growth.record(now - timedelta(minutes=10), 100)
    growth.record(now, 125)

    alerts = evaluate_alerts(_snapshot(total=125), _empty_ctx(), config, growth)
    assert "queue_growth" in _codes(alerts)


def test_queue_growth_no_alert_when_small_increase() -> None:
    config = _default_config()
    growth = QueueGrowthTracker(window_seconds=900)
    now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
    growth.record(now - timedelta(minutes=5), 100)
    growth.record(now, 105)

    alerts = evaluate_alerts(_snapshot(total=105), _empty_ctx(), config, growth)
    assert "queue_growth" not in _codes(alerts)


def test_oldest_queue_stale_alert() -> None:
    config = _default_config()
    growth = QueueGrowthTracker(window_seconds=900)
    alerts = evaluate_alerts(
        _snapshot(oldest_age=4000), _empty_ctx(), config, growth
    )
    assert "oldest_queue_stale" in _codes(alerts)


def test_high_postpone_alert() -> None:
    config = _default_config()
    growth = QueueGrowthTracker(window_seconds=900)
    ctx = AlertContext(
        high_postpone_tasks=(
            HighPostponeTaskRow(
                task_id=123,
                task_type_code="parser_add_channel",
                postpone_count=15,
            ),
        )
    )
    alerts = evaluate_alerts(_snapshot(), ctx, config, growth)
    matching = [a for a in alerts if a.code == "high_postpone"]
    assert len(matching) == 1
    assert matching[0].scope_key == "task:123"


def test_high_postpone_not_in_context_means_no_alert() -> None:
    config = _default_config()
    growth = QueueGrowthTracker(window_seconds=900)
    alerts = evaluate_alerts(_snapshot(), _empty_ctx(), config, growth)
    assert "high_postpone" not in _codes(alerts)


def test_no_active_accounts_error() -> None:
    config = _default_config()
    growth = QueueGrowthTracker(window_seconds=900)
    alerts = evaluate_alerts(
        _snapshot(active=0), _empty_ctx(), config, growth
    )
    assert "no_active_accounts" in _codes(alerts)


def test_task_type_error_spike() -> None:
    config = _default_config()
    growth = QueueGrowthTracker(window_seconds=900)
    ctx = AlertContext(
        task_type_error_rates=(
            ErrorRateRow(
                entity_id=7,
                attempts_last_hour=10,
                errors_last_hour=6,
                error_rate_percent=60.0,
            ),
        )
    )
    alerts = evaluate_alerts(_snapshot(), ctx, config, growth)
    assert "task_type_error_spike" in _codes(alerts)


def test_task_type_error_below_min_attempts_not_in_context() -> None:
    config = _default_config()
    growth = QueueGrowthTracker(window_seconds=900)
    alerts = evaluate_alerts(_snapshot(), _empty_ctx(), config, growth)
    assert "task_type_error_spike" not in _codes(alerts)


def test_account_error_spike() -> None:
    config = _default_config()
    growth = QueueGrowthTracker(window_seconds=900)
    ctx = AlertContext(
        account_error_rates=(
            ErrorRateRow(
                entity_id=42,
                attempts_last_hour=8,
                errors_last_hour=5,
                error_rate_percent=62.5,
            ),
        )
    )
    alerts = evaluate_alerts(_snapshot(), ctx, config, growth)
    assert "account_error_spike" in _codes(alerts)


def test_stuck_no_progress() -> None:
    config = _default_config()
    growth = QueueGrowthTracker(window_seconds=900)
    alerts = evaluate_alerts(
        _snapshot(stuck=2, done_5min=0), _empty_ctx(), config, growth
    )
    assert "stuck_no_progress" in _codes(alerts)


def test_queue_no_progress() -> None:
    config = _default_config()
    growth = QueueGrowthTracker(window_seconds=900)
    alerts = evaluate_alerts(
        _snapshot(total=5, done_5min=0), _empty_ctx(), config, growth
    )
    assert "queue_no_progress" in _codes(alerts)


@pytest.mark.asyncio
async def test_notifier_debounce_suppresses_duplicate() -> None:
    config = _default_config(cooldown_seconds=60)
    notifier = AlertNotifier(config)
    alert = Alert(
        code="queue_growth",
        severity="WARNING",
        message="test",
        scope_key="global",
        metrics_snapshot={"generated_at": "2026-06-25T12:00:00+00:00"},
    )
    assert await notifier.emit(alert) is True
    assert await notifier.emit(alert) is False


@pytest.mark.asyncio
async def test_notifier_different_scope_not_debounced() -> None:
    config = _default_config(cooldown_seconds=60)
    notifier = AlertNotifier(config)
    a1 = Alert(
        code="high_postpone",
        severity="WARNING",
        message="a",
        scope_key="task:1",
        metrics_snapshot={},
    )
    a2 = Alert(
        code="high_postpone",
        severity="WARNING",
        message="b",
        scope_key="task:2",
        metrics_snapshot={},
    )
    assert await notifier.emit(a1) is True
    assert await notifier.emit(a2) is True


@pytest.mark.asyncio
async def test_notifier_webhook_post() -> None:
    config = _default_config(webhook_url="https://example.test/hook")
    notifier = AlertNotifier(config)
    alert = Alert(
        code="no_active_accounts",
        severity="ERROR",
        message="нет аккаунтов",
        scope_key="global",
        metrics_snapshot={"generated_at": "2026-06-25T12:00:00+00:00"},
    )

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        assert await notifier.emit(alert) is True

    mock_session.post.assert_called_once()
    call_kwargs = mock_session.post.call_args.kwargs
    assert call_kwargs["json"]["alert_code"] == "no_active_accounts"


@pytest.mark.asyncio
async def test_run_tick_disabled_alerts() -> None:
    config = _default_config(enabled=False)
    growth = QueueGrowthTracker(window_seconds=900)
    notifier = AlertNotifier(config)
    repo = MagicMock()
    repo.fetch_alert_context = AsyncMock(
        return_value=(
            _snapshot(total=100, done_5min=0),
            _empty_ctx(),
        )
    )
    emitted = await run_tick(repo, config, growth, notifier)
    assert emitted == 0


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_alert_context_high_postpone(pg_pool) -> None:
    from tests.pg_cleanup import cleanup_queue_test_data

    prefix = f"test_g4_{uuid.uuid4().hex}"
    dedup = f"{prefix}_postpone"
    await cleanup_queue_test_data(dedup_key_like=f"{prefix}%")

    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO task_queue (
                task_type_id, task_type_code, status, priority, dedup_key,
                max_attempts, postpone_count
            )
            SELECT id, code, 'scheduled', $1, $2, max_attempts, 999
            FROM task_types WHERE code = 'parser_add_channel'
            """,
            TEST_ISOLATION_PRIORITY,
            dedup,
        )

    config = AlertConfig(high_postpone_min=10)
    from app_balance.queue.monitoring.metrics_repo import MetricsRepo

    snapshot, ctx = await MetricsRepo().fetch_alert_context(config)
    growth = QueueGrowthTracker(window_seconds=900)
    alerts = evaluate_alerts(snapshot, ctx, config, growth)
    assert any(a.code == "high_postpone" for a in alerts)

    await cleanup_queue_test_data(dedup_key_like=f"{prefix}%")
