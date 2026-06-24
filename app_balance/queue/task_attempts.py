"""B9 — история попыток выполнения задач (ТЗ §10; план B9).

Схема: DB/BD_schema.sql § task_attempts + idx_task_attempts_task_number.
insert — запись in-flight (status=running); finish — terminal status + finished_at.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from app_balance.queue.db import acquire

AttemptStatus = Literal["running", "success", "error", "timeout"]
AttemptFinishStatus = Literal["success", "error", "timeout"]

_INSERT_SQL = """
INSERT INTO task_attempts (
    task_id, task_type_id, account_id,
    source_account_id, target_account_id,
    attempt_number, status, started_at
) VALUES ($1, $2, $3, $4, $5, $6, 'running', COALESCE($7, now()))
RETURNING id
"""

_FINISH_SQL = """
UPDATE task_attempts
SET status = $2,
    error_code = $3,
    error_message = $4,
    finished_at = COALESCE($5, now())
WHERE id = $1
  AND status = 'running'
  AND finished_at IS NULL
"""


class TaskAttemptsRepo:
    """INSERT попытки и финализация результата (B9)."""

    async def insert(
        self,
        *,
        task_id: int,
        task_type_id: int,
        account_id: int,
        attempt_number: int,
        source_account_id: int | None = None,
        target_account_id: int | None = None,
        started_at: datetime | None = None,
    ) -> int:
        async with acquire() as conn:
            return await conn.fetchval(
                _INSERT_SQL,
                task_id,
                task_type_id,
                account_id,
                source_account_id,
                target_account_id,
                attempt_number,
                started_at,
            )

    async def finish(
        self,
        attempt_id: int,
        *,
        status: AttemptFinishStatus,
        error_code: str | None = None,
        error_message: str | None = None,
        finished_at: datetime | None = None,
    ) -> bool:
        async with acquire() as conn:
            result = await conn.execute(
                _FINISH_SQL,
                attempt_id,
                status,
                error_code,
                error_message,
                finished_at,
            )
            return int(result.split()[-1]) == 1
