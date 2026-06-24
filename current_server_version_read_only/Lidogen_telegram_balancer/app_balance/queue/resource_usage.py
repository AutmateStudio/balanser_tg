"""B8 — учёт расхода ресурса per-op (ТЗ §7, §11, §26.3; план B8).

Схема: DB/BD_schema.sql § account_resource_usage (op_type_id + units),
view v_account_op_usage_last_hour / v_account_resource_summary.
effective_rph = rph_limit × (1 − reserve_percent/100);
available_percent = (effective_rph − used_last_hour) / effective_rph × 100.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app_balance.queue.db import acquire
from app_balance.queue.per_op_reading import TaskOpAccountRole, TaskType


@dataclass(frozen=True, slots=True)
class OpAvailability:
    account_id: int
    op_type_id: int
    op_code: str
    effective_rph: int
    used_last_hour: int
    available_resource: int
    available_resource_percent: float


_INSERT_SQL = """
INSERT INTO account_resource_usage (
    account_id, op_type_id, task_id, task_attempt_id, task_type_id, units
) VALUES ($1, $2, $3, $4, $5, $6)
RETURNING id
"""

_COUNT_LAST_HOUR_SQL = """
SELECT COALESCE(SUM(units), 0)::int
FROM account_resource_usage
WHERE account_id = $1
  AND op_type_id = $2
  AND created_at >= now() - interval '1 hour'
"""

_OP_AVAILABILITY_SQL = """
SELECT
    account_id, op_type_id, op_code, effective_rph,
    used_last_hour, available_resource, available_resource_percent
FROM v_account_op_usage_last_hour
WHERE account_id = $1 AND op_type_id = $2
"""

_WORST_AVAILABLE_SQL = """
SELECT worst_available_percent
FROM v_account_resource_summary
WHERE account_id = $1
"""


class ResourceUsageRepo:
    """INSERT расхода и подсчёт доступного ресурса по op (B8)."""

    async def insert(
        self,
        account_id: int,
        op_type_id: int,
        task_id: int,
        task_type_id: int,
        units: int = 1,
        task_attempt_id: int | None = None,
    ) -> int:
        async with acquire() as conn:
            return await conn.fetchval(
                _INSERT_SQL,
                account_id,
                op_type_id,
                task_id,
                task_attempt_id,
                task_type_id,
                units,
            )

    async def record_for_task(
        self,
        *,
        task_type: TaskType,
        task_id: int,
        accounts_by_role: Mapping[TaskOpAccountRole, int],
        task_attempt_id: int | None = None,
    ) -> list[int]:
        """D5 — INSERT расхода per-op до Telethon RPC (ТЗ §7.3)."""
        inserted: list[int] = []
        for op in task_type.ops:
            if not op.op_is_enabled:
                continue
            account_id = accounts_by_role.get(op.account_role)
            if account_id is None:
                raise ValueError(f"no account for role {op.account_role!r}")
            row_id = await self.insert(
                account_id,
                op.op_type_id,
                task_id,
                task_type.id,
                units=op.units_per_execution,
                task_attempt_id=task_attempt_id,
            )
            inserted.append(row_id)
        return inserted

    async def count_last_hour(self, account_id: int, op_type_id: int) -> int:
        async with acquire() as conn:
            return await conn.fetchval(_COUNT_LAST_HOUR_SQL, account_id, op_type_id)

    async def op_availability(
        self, account_id: int, op_type_id: int
    ) -> OpAvailability | None:
        async with acquire() as conn:
            row = await conn.fetchrow(_OP_AVAILABILITY_SQL, account_id, op_type_id)
            if row is None:
                return None
            return OpAvailability(
                account_id=row["account_id"],
                op_type_id=row["op_type_id"],
                op_code=row["op_code"],
                effective_rph=row["effective_rph"],
                used_last_hour=row["used_last_hour"],
                available_resource=row["available_resource"],
                available_resource_percent=float(row["available_resource_percent"]),
            )

    async def worst_available_percent(self, account_id: int) -> float | None:
        """Минимальный available_percent среди enabled op аккаунта (C5/G7★)."""
        async with acquire() as conn:
            val = await conn.fetchval(_WORST_AVAILABLE_SQL, account_id)
            return float(val) if val is not None else None
