"""Юнит-тесты внеочередного account lease (без PG)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app_balance.queue.account_lease import (
    NoAccountAvailableError,
    acquire_best_account_lease,
)
from app_balance.queue.accounts import Account, BestPickResult
from app_balance.queue.per_op_reading import TaskType
from app_balance.queue.resource_check import ResourceCheckResult
from app_balance.queue.task_queue import CLAIMABLE_STATUSES


def _account(aid: int = 1, session: str = "s1") -> Account:
    return Account(
        id=aid,
        session_name=session,
        status="active",
        is_enabled=True,
        current_task_id=None,
        cooldown_until=None,
        last_used_at=None,
    )


def _task_type(code: str = "telegram_discover_leads") -> TaskType:
    return TaskType(
        id=99,
        code=code,
        name=code,
        description=None,
        is_enabled=True,
        default_priority=75,
        min_available_resource_percent=20,
        requires_specific_account=False,
        uses_two_accounts=False,
        max_attempts=3,
        retry_delay_seconds=60,
        retry_backoff_multiplier=Decimal("2"),
        max_retry_delay_seconds=1800,
        target_queue_size=None,
        max_postpone_count=100,
        task_timeout_seconds=600,
        created_at=None,
        updated_at=None,
        ops=(),
    )


@dataclass
class FakeAccounts:
    picks: list[BestPickResult | None] = field(default_factory=list)
    released: list[tuple[int, int | None]] = field(default_factory=list)
    _i: int = 0

    async def pick_best_and_reserve(self, task_id: int, **kwargs: Any) -> BestPickResult | None:
        if self._i >= len(self.picks):
            return None
        item = self.picks[self._i]
        self._i += 1
        return item

    async def release(self, account_id: int, task_id: int | None = None) -> None:
        self.released.append((account_id, task_id))


@dataclass
class FakeQueue:
    assigned: list[tuple[int, int]] = field(default_factory=list)
    completed: list[int] = field(default_factory=list)
    failed: list[tuple[int, str | None]] = field(default_factory=list)
    merged: list[tuple[int, dict]] = field(default_factory=list)

    async def assign_account(self, task_id: int, account_id: int) -> None:
        self.assigned.append((task_id, account_id))

    async def complete(self, task_id: int) -> bool:
        self.completed.append(task_id)
        return True

    async def fail(self, task_id: int, error: str | None = None) -> str | None:
        self.failed.append((task_id, error))
        return "failed"

    async def merge_payload(self, task_id: int, patch: dict) -> bool:
        self.merged.append((task_id, patch))
        return True


@dataclass
class FakeUsage:
    recorded: list[dict] = field(default_factory=list)

    async def record_for_task(self, **kwargs: Any) -> list[int]:
        self.recorded.append(kwargs)
        return [1]


@dataclass
class FakeTypes:
    task_type: TaskType | None

    async def get_by_code(self, code: str) -> TaskType | None:
        if self.task_type and self.task_type.code == code:
            return self.task_type
        return None


@pytest.mark.asyncio
async def test_claimable_statuses_exclude_in_progress() -> None:
    assert "in_progress" not in CLAIMABLE_STATUSES
    assert set(CLAIMABLE_STATUSES) == {"queued", "scheduled", "retry"}


@pytest.mark.asyncio
async def test_lease_success_releases_and_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    acc = _account(7, "alive")
    accounts = FakeAccounts(
        picks=[BestPickResult(account=acc, availability_percent=88.0)]
    )
    queue = FakeQueue()
    usage = FakeUsage()
    types = FakeTypes(_task_type())

    async def fake_insert(**kwargs: Any) -> int:
        return 555

    monkeypatch.setattr(
        "app_balance.queue.account_lease._insert_direct_lease_task",
        fake_insert,
    )

    class OkChecker:
        def __init__(self, _usage: Any) -> None:
            pass

        async def check_account(self, account_id: int, task_type: TaskType, **kwargs: Any):
            return ResourceCheckResult(ok=True, threshold=20, account_id=account_id)

    monkeypatch.setattr("app_balance.queue.account_lease.ResourceChecker", OkChecker)

    async with acquire_best_account_lease(
        "telegram_discover_leads",
        created_by="test",
        accounts_repo=accounts,  # type: ignore[arg-type]
        queue_repo=queue,  # type: ignore[arg-type]
        usage_repo=usage,  # type: ignore[arg-type]
        task_types_repo=types,  # type: ignore[arg-type]
    ) as lease:
        assert lease.task_id == 555
        assert lease.session_name == "alive"
        assert lease.availability_percent == 88.0

    assert accounts.released == [(7, 555)]
    assert queue.completed == [555]
    assert queue.failed == []
    assert usage.recorded


@pytest.mark.asyncio
async def test_lease_exception_fails_task_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acc = _account(3, "x")
    accounts = FakeAccounts(
        picks=[BestPickResult(account=acc, availability_percent=50.0)]
    )
    queue = FakeQueue()
    usage = FakeUsage()
    types = FakeTypes(_task_type())

    monkeypatch.setattr(
        "app_balance.queue.account_lease._insert_direct_lease_task",
        AsyncMock(return_value=901),
    )

    class OkChecker:
        def __init__(self, _usage: Any) -> None:
            pass

        async def check_account(self, account_id: int, task_type: TaskType, **kwargs: Any):
            return ResourceCheckResult(ok=True, threshold=20)

    monkeypatch.setattr("app_balance.queue.account_lease.ResourceChecker", OkChecker)

    with pytest.raises(RuntimeError, match="boom"):
        async with acquire_best_account_lease(
            "telegram_discover_leads",
            created_by="test",
            accounts_repo=accounts,  # type: ignore[arg-type]
            queue_repo=queue,  # type: ignore[arg-type]
            usage_repo=usage,  # type: ignore[arg-type]
            task_types_repo=types,  # type: ignore[arg-type]
        ):
            raise RuntimeError("boom")

    assert accounts.released == [(3, 901)]
    assert queue.failed
    assert queue.failed[0][0] == 901
    assert queue.completed == []


@pytest.mark.asyncio
async def test_lease_skips_low_resource_then_picks_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    low = _account(1, "low")
    high = _account(2, "high")
    accounts = FakeAccounts(
        picks=[
            BestPickResult(account=low, availability_percent=90.0),
            BestPickResult(account=high, availability_percent=70.0),
        ]
    )
    queue = FakeQueue()
    usage = FakeUsage()
    types = FakeTypes(_task_type())

    monkeypatch.setattr(
        "app_balance.queue.account_lease._insert_direct_lease_task",
        AsyncMock(return_value=42),
    )

    class SelectiveChecker:
        def __init__(self, _usage: Any) -> None:
            pass

        async def check_account(self, account_id: int, task_type: TaskType, **kwargs: Any):
            if account_id == 1:
                return ResourceCheckResult(
                    ok=False,
                    threshold=20,
                    failing_op_code="messages.SearchGlobal",
                    available_percent=5.0,
                )
            return ResourceCheckResult(ok=True, threshold=20)

    monkeypatch.setattr(
        "app_balance.queue.account_lease.ResourceChecker", SelectiveChecker
    )

    async with acquire_best_account_lease(
        "telegram_discover_leads",
        created_by="test",
        accounts_repo=accounts,  # type: ignore[arg-type]
        queue_repo=queue,  # type: ignore[arg-type]
        usage_repo=usage,  # type: ignore[arg-type]
        task_types_repo=types,  # type: ignore[arg-type]
    ) as lease:
        assert lease.account.id == 2

    # low released after reject + high released after success
    assert (1, 42) in accounts.released
    assert (2, 42) in accounts.released
    assert queue.completed == [42]


@pytest.mark.asyncio
async def test_lease_no_account(monkeypatch: pytest.MonkeyPatch) -> None:
    accounts = FakeAccounts(picks=[None])
    queue = FakeQueue()
    types = FakeTypes(_task_type())

    monkeypatch.setattr(
        "app_balance.queue.account_lease._insert_direct_lease_task",
        AsyncMock(return_value=11),
    )

    with pytest.raises(NoAccountAvailableError):
        async with acquire_best_account_lease(
            "telegram_discover_leads",
            created_by="test",
            accounts_repo=accounts,  # type: ignore[arg-type]
            queue_repo=queue,  # type: ignore[arg-type]
            usage_repo=FakeUsage(),  # type: ignore[arg-type]
            task_types_repo=types,  # type: ignore[arg-type]
        ):
            pass

    assert queue.failed
    assert queue.failed[0][0] == 11
