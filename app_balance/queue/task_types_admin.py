"""Admin read/write для task-types RPH API (GET/PATCH /parser/queue/task-types)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app_balance.queue import db
from app_balance.queue.ops_catalog import RESOURCE_OPS
from app_balance.queue.per_op_reading import TaskType, TaskTypeOp, TaskTypesRepo
from app_balance.queue.primary_op import is_g6_audit_error_code, resolve_primary_op

_OPERATOR_MANUAL = "operator_manual"
_OPERATOR_RESET = "operator_reset"

_LAST_ADJUSTMENT_SQL = """
SELECT created_at, new_rph_limit, error_code, action
FROM resource_limit_adjustments
WHERE op_code = $1
ORDER BY created_at DESC
LIMIT 1
"""

_UPDATE_RPH_SQL = """
UPDATE resource_op_types
SET rph_limit = $2, updated_at = now()
WHERE id = $1
"""

_INSERT_OPERATOR_AUDIT_SQL = """
INSERT INTO resource_limit_adjustments (
  error_code, op_code, op_type_id, action,
  old_rph_limit, new_rph_limit, account_id,
  error_count, window_seconds
) VALUES ($1, $2, $3, 'reduce_rph', $4, $5, NULL, 0, 0)
"""


class TaskTypeNotFoundError(LookupError):
    """Неизвестный task_types.code."""


class TaskTypePatchValidationError(ValueError):
    """Невалидное тело PATCH."""


@dataclass(frozen=True, slots=True)
class TaskTypeRphInfo:
    """RPH-поля для ответа API."""

    rph_limit_effective: int
    rph_limit_default: int
    primary_op_code: str
    rph_auto_reduced: bool
    rph_reduced_at: datetime | None


@dataclass(frozen=True, slots=True)
class TaskTypeAdminView:
    """Полный снимок типа задачи для API."""

    code: str
    name: str
    description: str | None
    is_enabled: bool
    default_priority: int
    min_available_resource_percent: int
    target_queue_size: int | None
    max_attempts: int
    retry_delay_seconds: int
    max_postpone_count: int
    task_timeout_seconds: int
    rph: TaskTypeRphInfo

    def to_list_item_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "rph_limit_effective": self.rph.rph_limit_effective,
            "rph_limit_default": self.rph.rph_limit_default,
            "primary_op_code": self.rph.primary_op_code,
            "rph_auto_reduced": self.rph.rph_auto_reduced,
            "rph_reduced_at": (
                self.rph.rph_reduced_at.isoformat().replace("+00:00", "Z")
                if self.rph.rph_reduced_at is not None
                else None
            ),
        }

    def to_detail_dict(self) -> dict[str, Any]:
        base = self.to_list_item_dict()
        base.update(
            {
                "is_enabled": self.is_enabled,
                "default_priority": self.default_priority,
                "min_available_resource_percent": self.min_available_resource_percent,
                "target_queue_size": self.target_queue_size,
                "max_attempts": self.max_attempts,
                "retry_delay_seconds": self.retry_delay_seconds,
                "max_postpone_count": self.max_postpone_count,
                "task_timeout_seconds": self.task_timeout_seconds,
            }
        )
        return base


def _catalog_default_rph(op_code: str) -> int:
    definition = RESOURCE_OPS.get(op_code)
    if definition is None:
        raise ValueError(f"unknown op code in catalog: {op_code!r}")
    return definition.rph_limit


def _compute_rph_info(
    primary: TaskTypeOp,
    last_adjustment: Any | None,
) -> TaskTypeRphInfo:
    effective = primary.rph_limit
    default = _catalog_default_rph(primary.op_code)
    auto_reduced = False
    reduced_at: datetime | None = None

    if effective < default and last_adjustment is not None:
        error_code = str(last_adjustment["error_code"])
        action = str(last_adjustment["action"])
        new_rph = last_adjustment["new_rph_limit"]
        if (
            action == "reduce_rph"
            and is_g6_audit_error_code(error_code)
            and new_rph is not None
            and int(new_rph) == effective
        ):
            auto_reduced = True
            reduced_at = last_adjustment["created_at"]

    return TaskTypeRphInfo(
        rph_limit_effective=effective,
        rph_limit_default=default,
        primary_op_code=primary.op_code,
        rph_auto_reduced=auto_reduced,
        rph_reduced_at=reduced_at,
    )


def _task_type_to_view(task_type: TaskType, last_adjustment: Any | None) -> TaskTypeAdminView:
    primary = resolve_primary_op(task_type)
    rph = _compute_rph_info(primary, last_adjustment)
    return TaskTypeAdminView(
        code=task_type.code,
        name=task_type.name,
        description=task_type.description,
        is_enabled=task_type.is_enabled,
        default_priority=task_type.default_priority,
        min_available_resource_percent=task_type.min_available_resource_percent,
        target_queue_size=task_type.target_queue_size,
        max_attempts=task_type.max_attempts,
        retry_delay_seconds=task_type.retry_delay_seconds,
        max_postpone_count=task_type.max_postpone_count,
        task_timeout_seconds=task_type.task_timeout_seconds,
        rph=rph,
    )


class TaskTypesAdminRepo:
    """Чтение и PATCH RPH для task-types admin API."""

    def __init__(self, task_types: TaskTypesRepo | None = None) -> None:
        self._task_types = task_types or TaskTypesRepo()

    async def list_all(self) -> list[TaskTypeAdminView]:
        task_types = await self._task_types.list_all()
        async with db.acquire() as conn:
            views: list[TaskTypeAdminView] = []
            for task_type in task_types:
                primary = resolve_primary_op(task_type)
                last_adj = await conn.fetchrow(_LAST_ADJUSTMENT_SQL, primary.op_code)
                views.append(_task_type_to_view(task_type, last_adj))
            return views

    async def get_by_code(self, code: str) -> TaskTypeAdminView | None:
        task_type = await self._task_types.get_by_code(code)
        if task_type is None:
            return None
        primary = resolve_primary_op(task_type)
        async with db.acquire() as conn:
            last_adj = await conn.fetchrow(_LAST_ADJUSTMENT_SQL, primary.op_code)
        return _task_type_to_view(task_type, last_adj)

    async def patch_rph(
        self,
        code: str,
        *,
        rph_limit: int | None = None,
        reset_rph_to_default: bool | None = None,
    ) -> TaskTypeAdminView:
        if reset_rph_to_default and rph_limit is not None:
            raise TaskTypePatchValidationError(
                "Нельзя одновременно задать rph_limit и reset_rph_to_default"
            )
        if not reset_rph_to_default and rph_limit is None:
            raise TaskTypePatchValidationError("Тело запроса пустое")
        if rph_limit is not None and rph_limit < 1:
            raise TaskTypePatchValidationError("rph_limit должен быть ≥ 1")

        task_type = await self._task_types.get_by_code(code)
        if task_type is None:
            raise TaskTypeNotFoundError(code)

        primary = resolve_primary_op(task_type)
        old_rph = primary.rph_limit

        if reset_rph_to_default:
            new_rph = _catalog_default_rph(primary.op_code)
            audit_code = _OPERATOR_RESET
        else:
            assert rph_limit is not None
            new_rph = rph_limit
            audit_code = _OPERATOR_MANUAL

        async with db.transaction() as conn:
            await conn.execute(_UPDATE_RPH_SQL, primary.op_type_id, new_rph)
            if old_rph != new_rph:
                await conn.execute(
                    _INSERT_OPERATOR_AUDIT_SQL,
                    audit_code,
                    primary.op_code,
                    primary.op_type_id,
                    old_rph,
                    new_rph,
                )
            last_adj = await conn.fetchrow(_LAST_ADJUSTMENT_SQL, primary.op_code)

        refreshed = _row_to_task_type_with_updated_rph(task_type, primary.op_code, new_rph)
        return _task_type_to_view(refreshed, last_adj)


def _row_to_task_type_with_updated_rph(
    task_type: TaskType,
    primary_op_code: str,
    new_rph: int,
) -> TaskType:
    """Копия TaskType с обновлённым rph_limit у primary op."""
    updated_ops: list[TaskTypeOp] = []
    for op in task_type.ops:
        if op.op_code == primary_op_code:
            updated_ops.append(
                TaskTypeOp(
                    task_type_op_id=op.task_type_op_id,
                    op_type_id=op.op_type_id,
                    op_code=op.op_code,
                    op_name=op.op_name,
                    units_per_execution=op.units_per_execution,
                    account_role=op.account_role,
                    rph_limit=new_rph,
                    reserve_percent=op.reserve_percent,
                    op_is_enabled=op.op_is_enabled,
                )
            )
        else:
            updated_ops.append(op)
    return TaskType(
        id=task_type.id,
        code=task_type.code,
        name=task_type.name,
        description=task_type.description,
        is_enabled=task_type.is_enabled,
        default_priority=task_type.default_priority,
        min_available_resource_percent=task_type.min_available_resource_percent,
        requires_specific_account=task_type.requires_specific_account,
        uses_two_accounts=task_type.uses_two_accounts,
        max_attempts=task_type.max_attempts,
        retry_delay_seconds=task_type.retry_delay_seconds,
        retry_backoff_multiplier=task_type.retry_backoff_multiplier,
        max_retry_delay_seconds=task_type.max_retry_delay_seconds,
        target_queue_size=task_type.target_queue_size,
        max_postpone_count=task_type.max_postpone_count,
        task_timeout_seconds=task_type.task_timeout_seconds,
        created_at=task_type.created_at,
        updated_at=task_type.updated_at,
        ops=tuple(updated_ops),
    )
