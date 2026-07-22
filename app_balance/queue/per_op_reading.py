"""B2 — чтение task_types + task_type_ops + resource_op_types (per-op §0.5).

Схема таблиц: DB/BD_schema.sql, DB/A8_integrate_main_db.sql (слой очереди поверх
Lidogen_main_DB / lead_monitor). Базовый дамп Lidogen_main_DB.sql этих таблиц не содержит.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Sequence

import asyncpg

from app_balance.queue.db import acquire

TaskOpAccountRole = Literal["primary", "source", "target"]

_TASK_TYPE_SELECT = """
SELECT
    id,
    code,
    name,
    description,
    is_enabled,
    default_priority,
    min_available_resource_percent,
    requires_specific_account,
    uses_two_accounts,
    max_attempts,
    retry_delay_seconds,
    retry_backoff_multiplier,
    max_retry_delay_seconds,
    target_queue_size,
    max_postpone_count,
    task_timeout_seconds,
    created_at,
    updated_at
FROM task_types
"""

_OPS_SELECT = """
SELECT
    tto.id AS task_type_op_id,
    tto.op_type_id,
    rot.code AS op_code,
    rot.name AS op_name,
    tto.units_per_execution,
    tto.account_role::text AS account_role,
    rot.rph_limit,
    rot.reserve_percent,
    rot.is_enabled AS op_is_enabled
FROM task_type_ops tto
JOIN resource_op_types rot ON rot.id = tto.op_type_id
WHERE tto.task_type_id = $1
ORDER BY tto.account_role, rot.code
"""


@dataclass(frozen=True, slots=True)
class TaskTypeOp:
    """Строка task_type_ops + resource_op_types (BD_schema § task_type_ops)."""

    task_type_op_id: int
    op_type_id: int
    op_code: str
    op_name: str | None
    units_per_execution: int
    account_role: TaskOpAccountRole
    rph_limit: int
    reserve_percent: Decimal
    op_is_enabled: bool


@dataclass(frozen=True, slots=True)
class TaskType:
    """Строка task_types с подгруженным op-составом (BD_schema § task_types)."""

    id: int
    code: str
    name: str
    description: str | None
    is_enabled: bool
    default_priority: int
    min_available_resource_percent: int
    requires_specific_account: bool
    uses_two_accounts: bool
    max_attempts: int
    retry_delay_seconds: int
    retry_backoff_multiplier: Decimal
    max_retry_delay_seconds: int
    target_queue_size: int | None
    max_postpone_count: int
    task_timeout_seconds: int
    created_at: object
    updated_at: object
    ops: tuple[TaskTypeOp, ...] = field(default_factory=tuple)


def _row_to_task_type(row: asyncpg.Record, ops: Sequence[TaskTypeOp]) -> TaskType:
    return TaskType(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        description=row["description"],
        is_enabled=row["is_enabled"],
        default_priority=row["default_priority"],
        min_available_resource_percent=row["min_available_resource_percent"],
        requires_specific_account=row["requires_specific_account"],
        uses_two_accounts=row["uses_two_accounts"],
        max_attempts=row["max_attempts"],
        retry_delay_seconds=row["retry_delay_seconds"],
        retry_backoff_multiplier=row["retry_backoff_multiplier"],
        max_retry_delay_seconds=row["max_retry_delay_seconds"],
        target_queue_size=row["target_queue_size"],
        max_postpone_count=row["max_postpone_count"],
        task_timeout_seconds=row["task_timeout_seconds"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        ops=tuple(ops),
    )


def _row_to_op(row: asyncpg.Record) -> TaskTypeOp:
    role = row["account_role"]
    return TaskTypeOp(
        task_type_op_id=row["task_type_op_id"],
        op_type_id=row["op_type_id"],
        op_code=row["op_code"],
        op_name=row["op_name"],
        units_per_execution=row["units_per_execution"],
        account_role=role,
        rph_limit=row["rph_limit"],
        reserve_percent=row["reserve_percent"],
        op_is_enabled=row["op_is_enabled"],
    )


async def _fetch_ops(conn: asyncpg.Connection, task_type_id: int) -> list[TaskTypeOp]:
    rows = await conn.fetch(_OPS_SELECT, task_type_id)
    return [_row_to_op(row) for row in rows]


class TaskTypesRepo:
    """Read-only репозиторий типов задач (B2)."""

    async def get_by_code(self, code: str) -> TaskType | None:
        async with acquire() as conn:
            row = await conn.fetchrow(
                f"{_TASK_TYPE_SELECT} WHERE code = $1",
                code,
            )
            if row is None:
                return None
            ops = await _fetch_ops(conn, row["id"])
            return _row_to_task_type(row, ops)

    async def list_enabled(self) -> list[TaskType]:
        async with acquire() as conn:
            rows = await conn.fetch(
                f"{_TASK_TYPE_SELECT} WHERE is_enabled = true "
                "ORDER BY default_priority DESC, code ASC"
            )
            result: list[TaskType] = []
            for row in rows:
                ops = await _fetch_ops(conn, row["id"])
                result.append(_row_to_task_type(row, ops))
            return result

    async def list_all(self) -> list[TaskType]:
        """Все типы задач (включая disabled), для admin API task-types."""
        async with acquire() as conn:
            rows = await conn.fetch(
                f"{_TASK_TYPE_SELECT} ORDER BY default_priority DESC, code ASC"
            )
            result: list[TaskType] = []
            for row in rows:
                ops = await _fetch_ops(conn, row["id"])
                result.append(_row_to_task_type(row, ops))
            return result
