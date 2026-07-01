"""GET/PATCH /queue/task-types — RPH лимиты по типам задач (§4.2 task-types API TZ)."""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app_balance.queue.task_types_admin import (
    TaskTypeNotFoundError,
    TaskTypePatchValidationError,
    TaskTypesAdminRepo,
)
from discovery_api.config import get_use_pg_queue

_repo = TaskTypesAdminRepo()


class TaskTypeListItemResponse(BaseModel):
    code: str
    name: str
    description: str | None = None
    rph_limit_effective: int = Field(ge=1)
    rph_limit_default: int = Field(ge=1)
    primary_op_code: str
    rph_auto_reduced: bool
    rph_reduced_at: datetime | None = None


class TaskTypeDetailResponse(TaskTypeListItemResponse):
    is_enabled: bool
    default_priority: int
    min_available_resource_percent: int
    target_queue_size: int | None = None
    max_attempts: int
    retry_delay_seconds: int
    max_postpone_count: int
    task_timeout_seconds: int


class TaskTypePatchRequest(BaseModel):
    rph_limit: int | None = None
    reset_rph_to_default: bool | None = None


def _validate_patch_body(body: TaskTypePatchRequest) -> None:
    has_rph = body.rph_limit is not None
    has_reset = body.reset_rph_to_default is True
    if has_rph and has_reset:
        raise HTTPException(
            status_code=400,
            detail="Нельзя одновременно задать rph_limit и reset_rph_to_default",
        )
    if not has_rph and not has_reset:
        raise HTTPException(
            status_code=400,
            detail="Укажите rph_limit или reset_rph_to_default",
        )
    if body.rph_limit is not None and body.rph_limit < 1:
        raise HTTPException(
            status_code=400,
            detail="rph_limit должен быть ≥ 1",
        )


def _guard_pg_queue() -> None:
    if not get_use_pg_queue():
        raise HTTPException(
            status_code=503,
            detail="PG-очередь не включена (USE_PG_QUEUE=false)",
        )


async def list_task_types() -> list[TaskTypeListItemResponse]:
    _guard_pg_queue()
    try:
        views = await _repo.list_all()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [TaskTypeListItemResponse.model_validate(v.to_list_item_dict()) for v in views]


async def get_task_type(code: str) -> TaskTypeDetailResponse:
    _guard_pg_queue()
    try:
        view = await _repo.get_by_code(code)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if view is None:
        raise HTTPException(
            status_code=404,
            detail=f"Task type not found: {code}",
        )
    return TaskTypeDetailResponse.model_validate(view.to_detail_dict())


async def patch_task_type(code: str, body: TaskTypePatchRequest) -> TaskTypeDetailResponse:
    _guard_pg_queue()
    _validate_patch_body(body)
    try:
        view = await _repo.patch_rph(
            code,
            rph_limit=body.rph_limit,
            reset_rph_to_default=body.reset_rph_to_default,
        )
    except TaskTypeNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Task type not found: {code}",
        ) from None
    except TaskTypePatchValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return TaskTypeDetailResponse.model_validate(view.to_detail_dict())
