"""G4 — правила алертов §26.4 (pure, без I/O)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app_balance.queue.monitoring.config import AlertConfig
from app_balance.queue.monitoring.metrics_repo import AlertContext, MetricsSnapshot
from app_balance.queue.monitoring.queue_growth import QueueGrowthTracker


@dataclass(frozen=True, slots=True)
class Alert:
    code: str
    severity: str
    message: str
    scope_key: str
    metrics_snapshot: dict[str, Any]


def evaluate_alerts(
    snapshot: MetricsSnapshot,
    ctx: AlertContext,
    config: AlertConfig,
    growth: QueueGrowthTracker,
) -> list[Alert]:
    metrics = snapshot.to_response_dict()
    alerts: list[Alert] = []

    growth_pct = growth.growth_percent()
    if growth_pct is not None and growth_pct >= config.queue_growth_percent:
        alerts.append(
            Alert(
                code="queue_growth",
                severity="WARNING",
                message=(
                    f"Очередь выросла на {growth_pct:.1f}% за окно "
                    f"{config.queue_growth_window_seconds} с "
                    f"(порог {config.queue_growth_percent:.0f}%)"
                ),
                scope_key="global",
                metrics_snapshot=metrics,
            )
        )

    if snapshot.queue.oldest_queued_age_seconds > config.oldest_queued_max_seconds:
        alerts.append(
            Alert(
                code="oldest_queue_stale",
                severity="WARNING",
                message=(
                    f"Самая старая задача в очереди ждёт "
                    f"{snapshot.queue.oldest_queued_age_seconds} с "
                    f"(порог {config.oldest_queued_max_seconds} с)"
                ),
                scope_key="global",
                metrics_snapshot=metrics,
            )
        )

    for task in ctx.high_postpone_tasks:
        alerts.append(
            Alert(
                code="high_postpone",
                severity="WARNING",
                message=(
                    f"Задача id={task.task_id} ({task.task_type_code}) "
                    f"отложена {task.postpone_count} раз "
                    f"(порог {config.high_postpone_min})"
                ),
                scope_key=f"task:{task.task_id}",
                metrics_snapshot=metrics,
            )
        )

    if snapshot.accounts.active == 0:
        alerts.append(
            Alert(
                code="no_active_accounts",
                severity="ERROR",
                message="Нет активных аккаунтов (active_accounts_count=0)",
                scope_key="global",
                metrics_snapshot=metrics,
            )
        )

    for row in ctx.task_type_error_rates:
        alerts.append(
            Alert(
                code="task_type_error_spike",
                severity="ERROR",
                message=(
                    f"Высокая частота ошибок по типу задачи id={row.entity_id}: "
                    f"{row.error_rate_percent:.1f}% "
                    f"({row.errors_last_hour}/{row.attempts_last_hour} за час)"
                ),
                scope_key=f"task_type:{row.entity_id}",
                metrics_snapshot=metrics,
            )
        )

    for row in ctx.account_error_rates:
        alerts.append(
            Alert(
                code="account_error_spike",
                severity="ERROR",
                message=(
                    f"Высокая частота ошибок по аккаунту id={row.entity_id}: "
                    f"{row.error_rate_percent:.1f}% "
                    f"({row.errors_last_hour}/{row.attempts_last_hour} за час)"
                ),
                scope_key=f"account:{row.entity_id}",
                metrics_snapshot=metrics,
            )
        )

    if snapshot.queue.stuck_count > 0 and snapshot.queue.done_last_5_min == 0:
        alerts.append(
            Alert(
                code="stuck_no_progress",
                severity="ERROR",
                message=(
                    f"Есть зависшие задачи (stuck={snapshot.queue.stuck_count}), "
                    "но за 5 минут ничего не завершено"
                ),
                scope_key="global",
                metrics_snapshot=metrics,
            )
        )

    if snapshot.queue.total > 0 and snapshot.queue.done_last_5_min == 0:
        alerts.append(
            Alert(
                code="queue_no_progress",
                severity="ERROR",
                message=(
                    f"Активная очередь ({snapshot.queue.total} задач), "
                    "но за 5 минут ничего не завершено"
                ),
                scope_key="global",
                metrics_snapshot=metrics,
            )
        )

    return alerts
