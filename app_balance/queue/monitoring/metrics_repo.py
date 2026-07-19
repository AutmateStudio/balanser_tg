"""G3 — чтение мониторинговых VIEW → typed snapshot (ТЗ §26)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app_balance.queue import db
from app_balance.queue.monitoring.config import AlertConfig


def _int_val(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _float_val(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _bool_val(value: Any) -> bool:
    return bool(value)


def _rows_to_by_status(rows: list[Any]) -> dict[str, int]:
    return {str(row["status"]): _int_val(row["tasks_count"]) for row in rows}


def _rows_to_by_type(rows: list[Any]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for row in rows:
        task_type = str(row["task_type_code"])
        status = str(row["status"])
        count = _int_val(row["tasks_count"])
        result.setdefault(task_type, {})[status] = count
    return result


def _col(summary: Any, key: str, default: int = 0) -> int:
    """Безопасно читать колонку VIEW (до A19 часть полей может отсутствовать)."""
    try:
        return _int_val(summary[key])
    except (KeyError, IndexError):
        return default


@dataclass(frozen=True, slots=True)
class ChannelCapacityMetrics:
    active_accounts: int
    assigned_channels_total: int
    fleet_capacity: int
    usage_percent: float


@dataclass(frozen=True, slots=True)
class PerOpUsageRow:
    account_id: int
    session_name: str
    account_status: str
    op_type_id: int
    op_code: str
    effective_rph: int
    used_last_hour: int
    available_resource: int
    available_resource_percent: float


@dataclass(frozen=True, slots=True)
class AccountResourceRow:
    account_id: int
    session_name: str
    account_status: str
    worst_available_percent: float
    any_op_exhausted: bool
    exhausted_ops_count: int


@dataclass(frozen=True, slots=True)
class QueueFlowMetrics:
    """In → out за фиксированные окна 5/10 мин + pickable backlog."""

    enqueued_last_5_min: int
    enqueued_last_10_min: int
    done_last_5_min: int
    done_last_10_min: int
    failed_last_5_min: int
    failed_last_10_min: int
    attempts_last_5_min: int
    attempts_last_10_min: int
    pickable_now: int
    in_progress: int


@dataclass(frozen=True, slots=True)
class QueueMetrics:
    total: int
    by_status: dict[str, int]
    by_type: dict[str, dict[str, int]]
    oldest_queued_age_seconds: int
    stuck_count: int
    done_last_5_min: int
    flow: QueueFlowMetrics


@dataclass(frozen=True, slots=True)
class AccountsMetrics:
    active: int
    in_cooldown: int
    without_resource: int
    per_op: tuple[PerOpUsageRow, ...] = field(default_factory=tuple)
    worst_by_account: tuple[AccountResourceRow, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class AlertsPreview:
    high_postpone_count: int
    pickable_starved: bool = False


@dataclass(frozen=True, slots=True)
class HighPostponeTaskRow:
    task_id: int
    task_type_code: str
    postpone_count: int


@dataclass(frozen=True, slots=True)
class ErrorRateRow:
    entity_id: int
    attempts_last_hour: int
    errors_last_hour: int
    error_rate_percent: float
    session_name: str | None = None
    task_type_code: str | None = None


@dataclass(frozen=True, slots=True)
class ErrorRatesMetrics:
    by_task_type: tuple[ErrorRateRow, ...] = field(default_factory=tuple)
    by_account: tuple[ErrorRateRow, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class AlertContext:
    high_postpone_tasks: tuple[HighPostponeTaskRow, ...] = field(default_factory=tuple)
    task_type_error_rates: tuple[ErrorRateRow, ...] = field(default_factory=tuple)
    account_error_rates: tuple[ErrorRateRow, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    queue: QueueMetrics
    accounts: AccountsMetrics
    alerts_preview: AlertsPreview
    channels: ChannelCapacityMetrics
    error_rates: ErrorRatesMetrics
    generated_at: datetime

    def to_response_dict(self) -> dict[str, Any]:
        """JSON-сериализуемый dict по контракту §26 (G3) + flow/channels/error_rates."""
        flow = self.queue.flow
        return {
            "queue": {
                "total": self.queue.total,
                "by_status": dict(self.queue.by_status),
                "by_type": {
                    task_type: dict(statuses)
                    for task_type, statuses in self.queue.by_type.items()
                },
                "oldest_queued_age_seconds": self.queue.oldest_queued_age_seconds,
                "stuck_count": self.queue.stuck_count,
                "done_last_5_min": self.queue.done_last_5_min,
                "flow": {
                    "enqueued_last_5_min": flow.enqueued_last_5_min,
                    "enqueued_last_10_min": flow.enqueued_last_10_min,
                    "done_last_5_min": flow.done_last_5_min,
                    "done_last_10_min": flow.done_last_10_min,
                    "failed_last_5_min": flow.failed_last_5_min,
                    "failed_last_10_min": flow.failed_last_10_min,
                    "attempts_last_5_min": flow.attempts_last_5_min,
                    "attempts_last_10_min": flow.attempts_last_10_min,
                    "pickable_now": flow.pickable_now,
                    "in_progress": flow.in_progress,
                },
            },
            "accounts": {
                "active": self.accounts.active,
                "in_cooldown": self.accounts.in_cooldown,
                "without_resource": self.accounts.without_resource,
                "per_op": [
                    {
                        "account_id": row.account_id,
                        "session_name": row.session_name,
                        "account_status": row.account_status,
                        "op_type_id": row.op_type_id,
                        "op_code": row.op_code,
                        "effective_rph": row.effective_rph,
                        "used_last_hour": row.used_last_hour,
                        "available_resource": row.available_resource,
                        "available_resource_percent": row.available_resource_percent,
                    }
                    for row in self.accounts.per_op
                ],
                "worst_by_account": [
                    {
                        "account_id": row.account_id,
                        "session_name": row.session_name,
                        "account_status": row.account_status,
                        "worst_available_percent": row.worst_available_percent,
                        "any_op_exhausted": row.any_op_exhausted,
                        "exhausted_ops_count": row.exhausted_ops_count,
                    }
                    for row in self.accounts.worst_by_account
                ],
            },
            "alerts_preview": {
                "high_postpone_count": self.alerts_preview.high_postpone_count,
                "pickable_starved": self.alerts_preview.pickable_starved,
            },
            "channels": {
                "active_accounts": self.channels.active_accounts,
                "assigned_channels_total": self.channels.assigned_channels_total,
                "fleet_capacity": self.channels.fleet_capacity,
                "usage_percent": self.channels.usage_percent,
            },
            "error_rates": {
                "by_task_type": [
                    {
                        "entity_id": row.entity_id,
                        "task_type_code": row.task_type_code,
                        "attempts_last_hour": row.attempts_last_hour,
                        "errors_last_hour": row.errors_last_hour,
                        "error_rate_percent": row.error_rate_percent,
                    }
                    for row in self.error_rates.by_task_type
                ],
                "by_account": [
                    {
                        "entity_id": row.entity_id,
                        "session_name": row.session_name,
                        "attempts_last_hour": row.attempts_last_hour,
                        "errors_last_hour": row.errors_last_hour,
                        "error_rate_percent": row.error_rate_percent,
                    }
                    for row in self.error_rates.by_account
                ],
            },
            "generated_at": self.generated_at.isoformat(),
        }


def _build_channel_capacity(
    channel_row: Any,
    max_channels_per_session: int,
    *,
    fleet_capacity_override: int | None = None,
) -> ChannelCapacityMetrics:
    active_accounts = _int_val(channel_row["active_accounts_count"])
    assigned_total = _int_val(channel_row["assigned_channels_total"])
    if fleet_capacity_override is not None and fleet_capacity_override > 0:
        fleet_capacity = fleet_capacity_override
    else:
        fleet_capacity = active_accounts * max(1, max_channels_per_session)
    usage_percent = (
        (assigned_total / fleet_capacity * 100.0) if fleet_capacity > 0 else 0.0
    )
    return ChannelCapacityMetrics(
        active_accounts=active_accounts,
        assigned_channels_total=assigned_total,
        fleet_capacity=fleet_capacity,
        usage_percent=usage_percent,
    )


def _pickable_starved(flow: QueueFlowMetrics) -> bool:
    return (
        (flow.pickable_now > 0 or flow.enqueued_last_5_min > 0)
        and flow.done_last_5_min == 0
        and flow.attempts_last_5_min == 0
    )


def _build_flow(summary: Any, by_status: dict[str, int]) -> QueueFlowMetrics:
    done_5 = _col(summary, "done_tasks_last_5_min")
    return QueueFlowMetrics(
        enqueued_last_5_min=_col(summary, "enqueued_last_5_min"),
        enqueued_last_10_min=_col(summary, "enqueued_last_10_min"),
        done_last_5_min=done_5,
        done_last_10_min=_col(summary, "done_tasks_last_10_min"),
        failed_last_5_min=_col(summary, "failed_tasks_last_5_min"),
        failed_last_10_min=_col(summary, "failed_tasks_last_10_min"),
        attempts_last_5_min=_col(summary, "attempts_last_5_min"),
        attempts_last_10_min=_col(summary, "attempts_last_10_min"),
        pickable_now=_col(summary, "pickable_now"),
        in_progress=by_status.get("in_progress", _col(summary, "in_progress_count")),
    )


def _build_snapshot(
    *,
    summary: Any,
    overview: Any,
    by_status_rows: list[Any],
    by_type_rows: list[Any],
    per_op_rows: list[Any],
    worst_rows: list[Any],
    high_postpone_count: int,
    channels: ChannelCapacityMetrics,
    error_rates: ErrorRatesMetrics,
    generated_at: datetime,
) -> MetricsSnapshot:
    by_status = _rows_to_by_status(by_status_rows)
    flow = _build_flow(summary, by_status)
    queue = QueueMetrics(
        total=_int_val(summary["queue_size_total"]),
        by_status=by_status,
        by_type=_rows_to_by_type(by_type_rows),
        oldest_queued_age_seconds=_int_val(summary["oldest_queued_task_age_seconds"]),
        stuck_count=_int_val(summary["stuck_tasks_count"]),
        done_last_5_min=flow.done_last_5_min,
        flow=flow,
    )
    accounts = AccountsMetrics(
        active=_int_val(overview["active_accounts_count"]),
        in_cooldown=_int_val(overview["accounts_in_cooldown"]),
        without_resource=_int_val(overview["accounts_without_resource"]),
        per_op=tuple(
            PerOpUsageRow(
                account_id=_int_val(row["account_id"]),
                session_name=str(row["session_name"]),
                account_status=str(row["account_status"]),
                op_type_id=_int_val(row["op_type_id"]),
                op_code=str(row["op_code"]),
                effective_rph=_int_val(row["effective_rph"]),
                used_last_hour=_int_val(row["used_last_hour"]),
                available_resource=_int_val(row["available_resource"]),
                available_resource_percent=_float_val(row["available_resource_percent"]),
            )
            for row in per_op_rows
        ),
        worst_by_account=tuple(
            AccountResourceRow(
                account_id=_int_val(row["account_id"]),
                session_name=str(row["session_name"]),
                account_status=str(row["account_status"]),
                worst_available_percent=_float_val(row["worst_available_percent"]),
                any_op_exhausted=_bool_val(row["any_op_exhausted"]),
                exhausted_ops_count=_int_val(row["exhausted_ops_count"]),
            )
            for row in worst_rows
        ),
    )
    return MetricsSnapshot(
        queue=queue,
        accounts=accounts,
        alerts_preview=AlertsPreview(
            high_postpone_count=_int_val(high_postpone_count),
            pickable_starved=_pickable_starved(flow),
        ),
        channels=channels,
        error_rates=error_rates,
        generated_at=generated_at,
    )


class MetricsRepo:
    """Read-only доступ к мониторинговым VIEW (G1/G2)."""

    async def fetch_snapshot(self) -> MetricsSnapshot:
        snapshot, _ctx = await self.fetch_alert_context(AlertConfig.from_env())
        return snapshot

    async def fetch_alert_context(
        self, config: AlertConfig
    ) -> tuple[MetricsSnapshot, AlertContext]:
        generated_at = datetime.now(timezone.utc)
        async with db.acquire() as conn:
            summary = await conn.fetchrow("SELECT * FROM v_queue_metrics")
            by_status_rows = await conn.fetch(
                "SELECT status, tasks_count FROM v_queue_size_by_status"
            )
            by_type_rows = await conn.fetch(
                "SELECT task_type_code, status, tasks_count FROM v_queue_size_by_type"
            )
            overview = await conn.fetchrow("SELECT * FROM v_accounts_overview")
            channel_row = await conn.fetchrow("SELECT * FROM v_channel_capacity_usage")
            per_op_rows = await conn.fetch(
                """
                SELECT
                    account_id, session_name, account_status, op_type_id, op_code,
                    effective_rph, used_last_hour, available_resource,
                    available_resource_percent
                FROM v_account_op_usage_last_hour
                ORDER BY account_id, op_type_id
                """
            )
            worst_rows = await conn.fetch(
                """
                SELECT
                    account_id, session_name, account_status,
                    worst_available_percent, any_op_exhausted, exhausted_ops_count
                FROM v_account_resource_summary
                ORDER BY worst_available_percent ASC NULLS FIRST, account_id
                """
            )
            high_postpone_count = await conn.fetchval(
                "SELECT COUNT(*) FROM v_high_postpone_tasks"
            )
            high_postpone_rows = await conn.fetch(
                """
                SELECT id, task_type_code, postpone_count
                FROM v_high_postpone_tasks
                WHERE postpone_count >= $1
                ORDER BY postpone_count DESC
                LIMIT 50
                """,
                config.high_postpone_min,
            )
            task_type_error_all = await conn.fetch(
                """
                SELECT
                    v.task_type_id,
                    tt.code AS task_type_code,
                    v.attempts_last_hour,
                    v.errors_last_hour,
                    v.error_rate_percent
                FROM v_task_type_error_rate_last_hour v
                LEFT JOIN task_types tt ON tt.id = v.task_type_id
                ORDER BY v.error_rate_percent DESC, v.attempts_last_hour DESC
                LIMIT 100
                """
            )
            account_error_all = await conn.fetch(
                """
                SELECT
                    v.account_id,
                    a.session_name,
                    v.attempts_last_hour,
                    v.errors_last_hour,
                    v.error_rate_percent
                FROM v_account_error_rate_last_hour v
                LEFT JOIN accounts a ON a.id = v.account_id
                ORDER BY v.error_rate_percent DESC, v.attempts_last_hour DESC
                LIMIT 100
                """
            )
            task_type_error_rows = await conn.fetch(
                """
                SELECT task_type_id, attempts_last_hour, errors_last_hour,
                       error_rate_percent
                FROM v_task_type_error_rate_last_hour
                WHERE attempts_last_hour >= $1 AND error_rate_percent > $2
                """,
                config.error_rate_min_attempts,
                config.error_rate_min_percent,
            )
            account_error_rows = await conn.fetch(
                """
                SELECT account_id, attempts_last_hour, errors_last_hour,
                       error_rate_percent
                FROM v_account_error_rate_last_hour
                WHERE attempts_last_hour >= $1 AND error_rate_percent > $2
                """,
                config.error_rate_min_attempts,
                config.error_rate_min_percent,
            )

        if summary is None:
            raise RuntimeError("v_queue_metrics вернул пустой результат")
        if overview is None:
            raise RuntimeError("v_accounts_overview вернул пустой результат")
        if channel_row is None:
            raise RuntimeError("v_channel_capacity_usage вернул пустой результат")

        # Ёмкость флота: сумма per-account лимитов из account store (если доступен),
        # иначе active_accounts * global max_channels_per_session.
        fleet_override: int | None = None
        try:
            from discovery_api.account_registry import (
                eff_channel_limit_info,
                list_accounts,
            )

            total_cap = 0
            for rec in list_accounts():
                if rec.get("admin_blocked"):
                    continue
                limit, _src = eff_channel_limit_info(
                    str(rec.get("session_name") or ""),
                    config.max_channels_per_session,
                )
                total_cap += max(1, int(limit))
            if total_cap > 0:
                fleet_override = total_cap
        except Exception:
            fleet_override = None

        channels = _build_channel_capacity(
            channel_row,
            config.max_channels_per_session,
            fleet_capacity_override=fleet_override,
        )
        error_rates = ErrorRatesMetrics(
            by_task_type=tuple(
                ErrorRateRow(
                    entity_id=_int_val(row["task_type_id"]),
                    attempts_last_hour=_int_val(row["attempts_last_hour"]),
                    errors_last_hour=_int_val(row["errors_last_hour"]),
                    error_rate_percent=_float_val(row["error_rate_percent"]),
                    task_type_code=(
                        str(row["task_type_code"])
                        if row["task_type_code"] is not None
                        else None
                    ),
                )
                for row in task_type_error_all
            ),
            by_account=tuple(
                ErrorRateRow(
                    entity_id=_int_val(row["account_id"]),
                    attempts_last_hour=_int_val(row["attempts_last_hour"]),
                    errors_last_hour=_int_val(row["errors_last_hour"]),
                    error_rate_percent=_float_val(row["error_rate_percent"]),
                    session_name=(
                        str(row["session_name"])
                        if row["session_name"] is not None
                        else None
                    ),
                )
                for row in account_error_all
            ),
        )
        snapshot = _build_snapshot(
            summary=summary,
            overview=overview,
            by_status_rows=by_status_rows,
            by_type_rows=by_type_rows,
            per_op_rows=per_op_rows,
            worst_rows=worst_rows,
            high_postpone_count=_int_val(high_postpone_count),
            channels=channels,
            error_rates=error_rates,
            generated_at=generated_at,
        )
        ctx = AlertContext(
            high_postpone_tasks=tuple(
                HighPostponeTaskRow(
                    task_id=_int_val(row["id"]),
                    task_type_code=str(row["task_type_code"]),
                    postpone_count=_int_val(row["postpone_count"]),
                )
                for row in high_postpone_rows
            ),
            task_type_error_rates=tuple(
                ErrorRateRow(
                    entity_id=_int_val(row["task_type_id"]),
                    attempts_last_hour=_int_val(row["attempts_last_hour"]),
                    errors_last_hour=_int_val(row["errors_last_hour"]),
                    error_rate_percent=_float_val(row["error_rate_percent"]),
                )
                for row in task_type_error_rows
            ),
            account_error_rates=tuple(
                ErrorRateRow(
                    entity_id=_int_val(row["account_id"]),
                    attempts_last_hour=_int_val(row["attempts_last_hour"]),
                    errors_last_hour=_int_val(row["errors_last_hour"]),
                    error_rate_percent=_float_val(row["error_rate_percent"]),
                )
                for row in account_error_rows
            ),
        )
        return snapshot, ctx


async def fetch_metrics_snapshot() -> MetricsSnapshot:
    return await MetricsRepo().fetch_snapshot()


async def fetch_alert_context(
    config: AlertConfig | None = None,
) -> tuple[MetricsSnapshot, AlertContext]:
    cfg = config or AlertConfig.from_env()
    return await MetricsRepo().fetch_alert_context(cfg)
