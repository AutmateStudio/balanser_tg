"""Внеочередной lease аккаунта с максимальным available RPH (без worker-очереди).

Создаёт эфемерную задачу status=in_progress (FK для accounts.current_task_id),
резервирует самый «живой» аккаунт по ops-scoped %, выполняет работу в HTTP,
затем release + complete/fail. Worker claim (queued/scheduled/retry) эту задачу
не подхватывает.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from app_balance.queue.accounts import Account, AccountsRepo
from app_balance.queue.db import acquire
from app_balance.queue.per_op_reading import TaskType, TaskTypesRepo
from app_balance.queue.resource_check import ResourceChecker, resolve_threshold
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import TaskQueueRepo

log = logging.getLogger(__name__)

DIRECT_LEASE_LOCKED_BY = "direct_lease"

_INSERT_DIRECT_LEASE_SQL = """
INSERT INTO task_queue (
    task_type_id,
    task_type_code,
    status,
    priority,
    payload,
    dedup_key,
    max_attempts,
    created_by,
    started_at,
    locked_by,
    locked_at,
    locked_until
) VALUES (
    $1, $2, 'in_progress', $3,
    $4::jsonb, $5, $6, $7,
    now(), $8, now(),
    now() + ($9 * interval '1 second')
)
RETURNING id
"""


class NoAccountAvailableError(Exception):
    """Нет свободного аккаунта с достаточным ресурсом для lease."""

    def __init__(self, message: str = "no_account_available") -> None:
        super().__init__(message)
        self.code = "no_account_available"


@dataclass(frozen=True, slots=True)
class AccountLease:
    task_id: int
    account: Account
    session_name: str
    availability_percent: float
    task_type: TaskType


async def _insert_direct_lease_task(
    *,
    task_type: TaskType,
    payload: dict[str, Any],
    created_by: str,
    lock_ttl_seconds: int = 3600,
) -> int:
    import json

    dedup_key = f"direct_lease:{uuid.uuid4()}"
    payload_json = json.dumps(
        {
            **(payload or {}),
            "direct_lease": True,
            "lease_id": dedup_key,
        }
    )
    async with acquire() as conn:
        task_id = await conn.fetchval(
            _INSERT_DIRECT_LEASE_SQL,
            task_type.id,
            task_type.code,
            task_type.default_priority,
            payload_json,
            dedup_key,
            task_type.max_attempts,
            created_by,
            DIRECT_LEASE_LOCKED_BY,
            max(60, int(lock_ttl_seconds)),
        )
    return int(task_id)


@asynccontextmanager
async def acquire_best_account_lease(
    task_type_code: str,
    *,
    created_by: str,
    payload: Optional[dict[str, Any]] = None,
    exclude_account_ids: Optional[frozenset[int]] = None,
    lock_ttl_seconds: int = 3600,
    accounts_repo: Optional[AccountsRepo] = None,
    queue_repo: Optional[TaskQueueRepo] = None,
    usage_repo: Optional[ResourceUsageRepo] = None,
    task_types_repo: Optional[TaskTypesRepo] = None,
) -> AsyncIterator[AccountLease]:
    """Context manager: лучший аккаунт → yield → release + complete/fail."""
    accounts = accounts_repo or AccountsRepo()
    queue = queue_repo or TaskQueueRepo()
    usage = usage_repo or ResourceUsageRepo()
    types_repo = task_types_repo or TaskTypesRepo()
    checker = ResourceChecker(usage)

    code = (task_type_code or "").strip()
    task_type = await types_repo.get_by_code(code)
    if task_type is None or not task_type.is_enabled:
        raise NoAccountAvailableError(
            f"task type '{code}' не найден или выключен"
        )

    threshold = float(resolve_threshold(task_type.min_available_resource_percent))
    task_id = await _insert_direct_lease_task(
        task_type=task_type,
        payload=dict(payload or {}),
        created_by=created_by,
        lock_ttl_seconds=lock_ttl_seconds,
    )

    rejected: set[int] = set(exclude_account_ids or ())
    account: Account | None = None
    availability = 0.0
    success = False
    error_message: str | None = None

    try:
        while True:
            pick = await accounts.pick_best_and_reserve(
                task_id,
                task_type_code=code,
                min_available_percent=threshold,
                exclude_account_ids=frozenset(rejected),
            )
            if pick is None:
                raise NoAccountAvailableError(
                    "нет свободного аккаунта с достаточным ресурсом"
                )

            check = await checker.check_account(pick.account.id, task_type)
            if not check.ok:
                log.info(
                    "direct_lease: аккаунт id=%s отклонён resource check "
                    "(op=%s avail=%s threshold=%s)",
                    pick.account.id,
                    check.failing_op_code,
                    check.available_percent,
                    check.threshold,
                )
                await accounts.release(pick.account.id, task_id)
                rejected.add(pick.account.id)
                continue

            account = pick.account
            availability = pick.availability_percent
            await queue.assign_account(task_id, account.id)
            break

        assert account is not None

        await usage.record_for_task(
            task_type=task_type,
            task_id=task_id,
            accounts_by_role={"primary": account.id},
        )

        lease = AccountLease(
            task_id=task_id,
            account=account,
            session_name=account.session_name,
            availability_percent=availability,
            task_type=task_type,
        )
        try:
            yield lease
            success = True
        except Exception as exc:
            error_message = str(exc)[:500]
            raise
    except NoAccountAvailableError as exc:
        error_message = str(exc)
        raise
    finally:
        if account is not None:
            await accounts.release(account.id, task_id)
        try:
            if success:
                await queue.merge_payload(
                    task_id,
                    {
                        "lease": {
                            "ok": True,
                            "account_id": account.id if account else None,
                            "session_name": account.session_name if account else None,
                            "availability_percent": availability,
                        }
                    },
                )
                await queue.complete(task_id)
            else:
                await queue.fail(
                    task_id,
                    error_message or "direct_lease_failed",
                )
        except Exception:  # noqa: BLE001
            log.exception(
                "direct_lease: не удалось закрыть задачу task_id=%s",
                task_id,
            )
