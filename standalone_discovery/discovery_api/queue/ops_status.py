"""GET /queue/watchdogs, /queue/alerts, /queue/resource-adjustments."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app_balance.queue.monitoring.alert_rules import evaluate_alerts
from app_balance.queue.monitoring.config import AlertConfig
from app_balance.queue.monitoring.error_detector_repo import ErrorDetectorRepo
from app_balance.queue.monitoring.metrics_repo import MetricsRepo
from app_balance.queue.monitoring.queue_growth import QueueGrowthTracker
from app_balance.queue.monitoring.threshold_rules import evaluate_threshold_alerts
from app_balance.queue.monitoring.watchdog_heartbeat import get_watchdog_registry
from discovery_api.config import get_use_pg_queue


def _require_pg_queue() -> None:
    if not get_use_pg_queue():
        raise HTTPException(
            status_code=503,
            detail="PG-очередь не включена (USE_PG_QUEUE=false)",
        )


class WatchdogItemResponse(BaseModel):
    name: str
    last_tick_at: str | None = None
    last_duration_ms: int | None = None
    last_result: dict[str, Any] = Field(default_factory=dict)
    last_error: str | None = None
    interval_seconds: float | None = None
    enabled: bool = False
    process: str | None = None
    stale: bool = True


class WatchdogsResponse(BaseModel):
    generated_at: str
    watchdogs: list[WatchdogItemResponse]


class AlertItemResponse(BaseModel):
    code: str
    severity: str
    message: str
    scope_key: str


class AlertsResponse(BaseModel):
    generated_at: str
    alerts: list[AlertItemResponse]


class ResourceAdjustmentResponse(BaseModel):
    id: int
    error_code: str
    op_code: str
    action: str
    old_rph_limit: int | None = None
    new_rph_limit: int | None = None
    account_id: int | None = None
    error_count: int
    created_at: str


class ResourceAdjustmentsResponse(BaseModel):
    generated_at: str
    total: int
    items: list[ResourceAdjustmentResponse]


async def get_queue_watchdogs() -> WatchdogsResponse:
    _require_pg_queue()
    registry = get_watchdog_registry()
    await registry.load_from_db()
    now = datetime.now(timezone.utc)
    return WatchdogsResponse(
        generated_at=now.isoformat(),
        watchdogs=[
            WatchdogItemResponse.model_validate(item)
            for item in registry.list_status()
        ],
    )


async def get_queue_alerts() -> AlertsResponse:
    _require_pg_queue()
    config = AlertConfig.from_env()
    try:
        snapshot, ctx = await MetricsRepo().fetch_alert_context(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    growth = QueueGrowthTracker(window_seconds=config.queue_growth_window_seconds)
    growth.record(snapshot.generated_at, snapshot.queue.total)
    alerts = evaluate_alerts(snapshot, ctx, config, growth)
    if config.threshold_enabled:
        alerts = [*alerts, *evaluate_threshold_alerts(snapshot, config)]

    return AlertsResponse(
        generated_at=snapshot.generated_at.isoformat(),
        alerts=[
            AlertItemResponse(
                code=a.code,
                severity=a.severity,
                message=a.message,
                scope_key=a.scope_key,
            )
            for a in alerts
        ],
    )


async def get_resource_adjustments(
    *,
    limit: int = 50,
    op_code: str | None = None,
    error_code: str | None = None,
) -> ResourceAdjustmentsResponse:
    _require_pg_queue()
    limit = max(1, min(limit, 200))
    try:
        rows = await ErrorDetectorRepo().list_adjustments(
            limit=limit,
            op_code=op_code,
            error_code=error_code,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    items = [
        ResourceAdjustmentResponse(
            id=row.id,
            error_code=row.error_code,
            op_code=row.op_code,
            action=row.action,
            old_rph_limit=row.old_rph_limit,
            new_rph_limit=row.new_rph_limit,
            account_id=row.account_id,
            error_count=row.error_count,
            created_at=row.created_at.isoformat(),
        )
        for row in rows
    ]
    return ResourceAdjustmentsResponse(
        generated_at=now.isoformat(),
        total=len(items),
        items=items,
    )
