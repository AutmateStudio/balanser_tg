"""G7★ — правила порогов загрузки каналов и per-op ресурса (pure, без I/O)."""
from __future__ import annotations

from app_balance.queue.monitoring.alert_rules import Alert
from app_balance.queue.monitoring.config import AlertConfig
from app_balance.queue.monitoring.metrics_repo import MetricsSnapshot


def evaluate_threshold_alerts(
    snapshot: MetricsSnapshot,
    config: AlertConfig,
) -> list[Alert]:
    if not config.threshold_enabled:
        return []

    metrics = snapshot.to_response_dict()
    alerts: list[Alert] = []
    channels = snapshot.channels

    if channels.fleet_capacity > 0:
        if channels.usage_percent >= config.threshold_channel_percent:
            alerts.append(
                Alert(
                    code="threshold_channel_capacity",
                    severity="WARNING",
                    message=(
                        f"Загрузка каналов {channels.usage_percent:.1f}% "
                        f"({channels.assigned_channels_total}/"
                        f"{channels.fleet_capacity} "
                        f"при {channels.active_accounts} active акк., "
                        f"лимит {config.max_channels_per_session}/акк.; "
                        f"порог {config.threshold_channel_percent:.0f}%)"
                    ),
                    scope_key="global",
                    metrics_snapshot=metrics,
                )
            )

    for account in snapshot.accounts.worst_by_account:
        if account.account_status != "active":
            continue
        if not account.any_op_exhausted and (
            account.worst_available_percent > config.threshold_resource_percent
        ):
            continue
        alerts.append(
            Alert(
                code="threshold_resource_exhausted",
                severity="ERROR",
                message=(
                    f"Аккаунт {account.session_name} (id={account.account_id}): "
                    f"ресурс исчерпан "
                    f"(worst={account.worst_available_percent:.1f}%, "
                    f"исчерпано op: {account.exhausted_ops_count})"
                ),
                scope_key=f"account:{account.account_id}",
                metrics_snapshot=metrics,
            )
        )

    return alerts
