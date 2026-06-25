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


class QueueMetricsResponse(BaseModel):
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, dict[str, int]] = Field(default_factory=dict)
    oldest_queued_age_seconds: int
    stuck_count: int
    done_last_5_min: int


class AccountsMetricsResponse(BaseModel):
    active: int
    in_cooldown: int
    without_resource: int
    per_op: list[PerOpUsageResponse] = Field(default_factory=list)
    worst_by_account: list[AccountResourceResponse] = Field(default_factory=list)


class AlertsPreviewResponse(BaseModel):
    high_postpone_count: int


class MetricsResponse(BaseModel):
    queue: QueueMetricsResponse
    accounts: AccountsMetricsResponse
    alerts_preview: AlertsPreviewResponse
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
