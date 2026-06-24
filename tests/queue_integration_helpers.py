"""Общие хелперы integration-тестов очереди (изоляция от фонового worker)."""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app_balance.queue import db
from app_balance.queue.task_queue import ClaimedTask, EnqueueInput, TaskQueueRepo
from tests.conftest import TEST_ISOLATION_PRIORITY

DEFAULT_PRIORITY = TEST_ISOLATION_PRIORITY


class AlwaysOkResourceChecker:
    """Integration: пропускает проверку RPH, когда тестируем не resource_check."""

    async def check_account(self, account_id: int, task_type, **kwargs):
        from app_balance.queue.resource_check import ResourceCheckResult

        return ResourceCheckResult(
            ok=True,
            threshold=task_type.min_available_resource_percent,
            account_id=account_id,
        )


async def insert_test_account(*, prefix: str) -> tuple[int, str]:
    session_name = f"{prefix}{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )
    return int(account_id), session_name


async def enqueue_isolated_task(
    *,
    prefix: str,
    task_type_code: str,
    account_id: int | None = None,
    payload: dict[str, Any] | None = None,
    max_attempts: int | None = None,
    attempt_count: int | None = None,
) -> int:
    res = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code=task_type_code,
            dedup_key=f"{prefix}{uuid.uuid4().hex}",
            priority=DEFAULT_PRIORITY,
            account_id=account_id,
            payload=payload or {"ref": "@integration_test"},
        )
    )
    assert res.created and res.task_id is not None
    task_id = int(res.task_id)
    if max_attempts is not None or attempt_count is not None:
        async with db.acquire() as conn:
            await conn.execute(
                """
                UPDATE task_queue
                SET max_attempts = COALESCE($2, max_attempts),
                    attempt_count = COALESCE($3, attempt_count)
                WHERE id = $1
                """,
                task_id,
                max_attempts,
                attempt_count,
            )
    return task_id


async def claim_isolated_task(
    task_id: int,
    *,
    locked_by: str,
) -> ClaimedTask | None:
    return await TaskQueueRepo().claim_by_id(task_id, locked_by=locked_by)


async def require_claimed_task(task_id: int, *, locked_by: str) -> ClaimedTask:
    claimed = await claim_isolated_task(task_id, locked_by=locked_by)
    if claimed is None:
        pytest.skip(
            f"задача id={task_id} недоступна для claim "
            "(фоновый queue-worker на shared PG)"
        )
    return claimed


async def reclaim_retry_task(task_id: int, *, locked_by: str) -> ClaimedTask:
    async with db.acquire() as conn:
        await conn.execute(
            """
            UPDATE task_queue
            SET run_after = now() - interval '1 second',
                locked_by = NULL,
                locked_at = NULL,
                locked_until = NULL
            WHERE id = $1 AND status = 'retry'
            """,
            task_id,
        )
    return await require_claimed_task(task_id, locked_by=locked_by)


async def load_in_progress_claimed(task_id: int) -> ClaimedTask:
    """Собирает ClaimedTask для уже in_progress задачи (без claim SQL)."""
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, task_type_id, task_type_code, priority, payload,
                   channel_id, account_id, source_account_id, target_account_id,
                   attempt_count, max_attempts, dedup_key, locked_by, locked_until
            FROM task_queue WHERE id = $1
            """,
            task_id,
        )
    assert row is not None
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ClaimedTask(
        id=int(row["id"]),
        task_type_id=int(row["task_type_id"]),
        task_type_code=str(row["task_type_code"]),
        priority=int(row["priority"]),
        payload=dict(payload or {}),
        channel_id=row["channel_id"],
        account_id=row["account_id"],
        source_account_id=row["source_account_id"],
        target_account_id=row["target_account_id"],
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        dedup_key=row["dedup_key"],
        locked_by=row["locked_by"],
        locked_until=row["locked_until"],
    )
