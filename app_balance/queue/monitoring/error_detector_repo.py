"""G6 — чтение паттернов ошибок и применение коррекций RPH per-op."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.monitoring.config import ErrorDetectorConfig

_FETCH_RECURRING_SQL = """
SELECT
  ta.error_code,
  rot.code AS op_code,
  rot.id AS op_type_id,
  rot.rph_limit AS current_rph_limit,
  count(DISTINCT ta.id)::int AS error_count,
  max(ta.started_at) AS last_error_at,
  (array_agg(ta.account_id ORDER BY ta.started_at DESC))[1]::bigint AS last_account_id
FROM task_attempts ta
INNER JOIN account_resource_usage aru ON aru.task_attempt_id = ta.id
INNER JOIN resource_op_types rot ON rot.id = aru.op_type_id
WHERE ta.status IN ('error', 'timeout')
  AND ta.error_code IS NOT NULL
  AND ta.started_at >= now() - ($1::int * interval '1 second')
GROUP BY ta.error_code, rot.code, rot.id, rot.rph_limit
HAVING count(DISTINCT ta.id) >= $2
"""

_COUNT_ADJUSTMENTS_SQL = """
SELECT count(*)::int
FROM resource_limit_adjustments
WHERE error_code = $1
  AND op_code = $2
  AND created_at >= now() - ($3::int * interval '1 second')
"""

_HAS_ADJUSTMENT_IN_WINDOW_SQL = """
SELECT EXISTS(
  SELECT 1 FROM resource_limit_adjustments
  WHERE error_code = $1
    AND op_code = $2
    AND created_at >= now() - ($3::int * interval '1 second')
)
"""

_INSERT_AUDIT_SQL = """
INSERT INTO resource_limit_adjustments (
  error_code, op_code, op_type_id, action,
  old_rph_limit, new_rph_limit, account_id,
  error_count, window_seconds
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
RETURNING id
"""

_UPDATE_RPH_SQL = """
UPDATE resource_op_types
SET rph_limit = $2, updated_at = now()
WHERE id = $1
"""

_DISABLE_OP_SQL = """
UPDATE resource_op_types
SET is_enabled = false, updated_at = now()
WHERE id = $1
"""

_SESSION_NAME_BY_ACCOUNT_SQL = """
SELECT session_name FROM accounts WHERE id = $1
"""


@dataclass(frozen=True, slots=True)
class RecurringErrorRow:
    error_code: str
    op_code: str
    op_type_id: int
    current_rph_limit: int
    error_count: int
    last_error_at: datetime
    last_account_id: int | None


ActionKind = Literal["reduce_rph", "disable_op"]
Severity = Literal["WARNING", "CRITICAL"]


@dataclass(frozen=True, slots=True)
class AdjustmentPlan:
    error_code: str
    op_code: str
    op_type_id: int
    action: ActionKind
    old_rph_limit: int
    new_rph_limit: int | None
    error_count: int
    account_id: int | None
    apply_cooldown: bool
    severity: Severity


class ErrorDetectorRepo:
    """G6 — агрегация ошибок и мутация resource_op_types + audit."""

    def __init__(self, accounts: AccountsRepo | None = None) -> None:
        self._accounts = accounts or AccountsRepo()

    async def fetch_recurring_errors(
        self, config: ErrorDetectorConfig
    ) -> list[RecurringErrorRow]:
        async with db.acquire() as conn:
            rows = await conn.fetch(
                _FETCH_RECURRING_SQL,
                config.window_seconds,
                config.min_count,
            )
        return [
            RecurringErrorRow(
                error_code=str(row["error_code"]),
                op_code=str(row["op_code"]),
                op_type_id=int(row["op_type_id"]),
                current_rph_limit=int(row["current_rph_limit"]),
                error_count=int(row["error_count"]),
                last_error_at=row["last_error_at"],
                last_account_id=(
                    int(row["last_account_id"])
                    if row["last_account_id"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    async def count_adjustments(
        self,
        error_code: str,
        op_code: str,
        window_seconds: int,
    ) -> int:
        async with db.acquire() as conn:
            val = await conn.fetchval(
                _COUNT_ADJUSTMENTS_SQL,
                error_code,
                op_code,
                window_seconds,
            )
        return int(val or 0)

    async def has_adjustment_in_window(
        self,
        error_code: str,
        op_code: str,
        window_seconds: int,
    ) -> bool:
        async with db.acquire() as conn:
            val = await conn.fetchval(
                _HAS_ADJUSTMENT_IN_WINDOW_SQL,
                error_code,
                op_code,
                window_seconds,
            )
        return bool(val)

    async def apply_plan(self, plan: AdjustmentPlan, config: ErrorDetectorConfig) -> None:
        async with db.transaction() as conn:
            if plan.action == "disable_op":
                await conn.execute(_DISABLE_OP_SQL, plan.op_type_id)
                await conn.fetchval(
                    _INSERT_AUDIT_SQL,
                    plan.error_code,
                    plan.op_code,
                    plan.op_type_id,
                    plan.action,
                    plan.old_rph_limit,
                    plan.new_rph_limit,
                    plan.account_id,
                    plan.error_count,
                    config.window_seconds,
                )
            else:
                if (
                    plan.new_rph_limit is not None
                    and plan.new_rph_limit != plan.old_rph_limit
                ):
                    await conn.execute(
                        _UPDATE_RPH_SQL, plan.op_type_id, plan.new_rph_limit
                    )
                await conn.fetchval(
                    _INSERT_AUDIT_SQL,
                    plan.error_code,
                    plan.op_code,
                    plan.op_type_id,
                    plan.action,
                    plan.old_rph_limit,
                    plan.new_rph_limit,
                    plan.account_id,
                    plan.error_count,
                    config.window_seconds,
                )

        if plan.apply_cooldown and plan.account_id is not None:
            async with db.acquire() as conn:
                session_name = await conn.fetchval(
                    _SESSION_NAME_BY_ACCOUNT_SQL, plan.account_id
                )
            if session_name:
                until = datetime.now(timezone.utc) + timedelta(
                    seconds=config.cooldown_seconds
                )
                await self._accounts.set_cooldown(str(session_name), until)
