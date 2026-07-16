"""G3 — GET /queue/metrics: агрегат мониторинговых VIEW (ТЗ §26)."""
from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app_balance.queue.monitoring.metrics_repo import fetch_metrics_snapshot
from discovery_api.config import get_use_pg_queue


class PerOpUsageResponse(BaseModel):
    account_id: int
    session_name: str
    account_status: str
    op_type_id: int
    op_code: str
    effective_rph: int
    used_last_hour: int
    available_resource: int
    available_resource_percent: float


class AccountResourceResponse(BaseModel):
    account_id: int
    session_name: str
    account_status: str
    worst_available_percent: float
    any_op_exhausted: bool
    exhausted_ops_count: int


class QueueFlowResponse(BaseModel):
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


class QueueMetricsResponse(BaseModel):
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, dict[str, int]] = Field(default_factory=dict)
    oldest_queued_age_seconds: int
    stuck_count: int
    done_last_5_min: int
    flow: QueueFlowResponse


class AccountsMetricsResponse(BaseModel):
    active: int
    in_cooldown: int
    without_resource: int
    per_op: list[PerOpUsageResponse] = Field(default_factory=list)
    worst_by_account: list[AccountResourceResponse] = Field(default_factory=list)


class AlertsPreviewResponse(BaseModel):
    high_postpone_count: int
    pickable_starved: bool = False


class ChannelsMetricsResponse(BaseModel):
    active_accounts: int
    assigned_channels_total: int
    fleet_capacity: int
    usage_percent: float


class TaskTypeErrorRateResponse(BaseModel):
    entity_id: int
    task_type_code: str | None = None
    attempts_last_hour: int
    errors_last_hour: int
    error_rate_percent: float


class AccountErrorRateResponse(BaseModel):
    entity_id: int
    session_name: str | None = None
    attempts_last_hour: int
    errors_last_hour: int
    error_rate_percent: float


class ErrorRatesResponse(BaseModel):
    by_task_type: list[TaskTypeErrorRateResponse] = Field(default_factory=list)
    by_account: list[AccountErrorRateResponse] = Field(default_factory=list)


class MetricsResponse(BaseModel):
    queue: QueueMetricsResponse
    accounts: AccountsMetricsResponse
    alerts_preview: AlertsPreviewResponse
    channels: ChannelsMetricsResponse
    error_rates: ErrorRatesResponse
    generated_at: str


async def get_queue_metrics() -> MetricsResponse:
    if not get_use_pg_queue():
        raise HTTPException(
            status_code=503,
            detail="PG-очередь не включена (USE_PG_QUEUE=false)",
        )
    try:
        snapshot = await fetch_metrics_snapshot()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return MetricsResponse.model_validate(snapshot.to_response_dict())
