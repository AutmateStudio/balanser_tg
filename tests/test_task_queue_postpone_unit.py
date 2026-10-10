"""B5 — юнит-тесты postpone без PostgreSQL."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app_balance.queue.task_queue import (
    TaskQueueRepo,
    _FAIL_SQL,
    _POSTPONE_LOCK_SQL,
    _POSTPONE_SQL,
)


def test_postpone_sql_sets_scheduled_and_clears_lock() -> None:
    sql = _POSTPONE_SQL.lower()
    assert "status = 'scheduled'" in sql
    assert "postpone_count = postpone_count + 1" in sql
    assert "locked_by = null" in sql
    assert "locked_at = null" in sql
    assert "locked_until = null" in sql
    assert "attempt_count" not in sql


def _patch_transaction(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    @asynccontextmanager
    async def fake_transaction():
        yield conn

    monkeypatch.setattr("app_balance.queue.task_queue.transaction", fake_transaction)


@pytest.mark.asyncio
async def test_postpone_executes_update(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={"postpone_count": 0, "max_postpone_count": 100}
    )
    conn.execute = AsyncMock(return_value="UPDATE 1")
    _patch_transaction(monkeypatch, conn)

    assert await TaskQueueRepo().postpone(42, delay_seconds=120, reason="нет аккаунта") is True
    conn.fetchrow.assert_awaited_once_with(_POSTPONE_LOCK_SQL, 42, None)
    conn.execute.assert_awaited_once_with(
        _POSTPONE_SQL, 42, 120, "нет аккаунта", None
    )


@pytest.mark.asyncio
async def test_postpone_default_delay_and_no_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={"postpone_count": 1, "max_postpone_count": 100}
    )
    conn.execute = AsyncMock(return_value="UPDATE 1")
    _patch_transaction(monkeypatch, conn)

    assert await TaskQueueRepo().postpone(7) is True
    conn.execute.assert_awaited_once_with(_POSTPONE_SQL, 7, 300, None, None)


@pytest.mark.asyncio
async def test_postpone_fails_when_max_postpone_count_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={"postpone_count": 100, "max_postpone_count": 100}
    )
    conn.fetchval = AsyncMock(return_value="failed")
    _patch_transaction(monkeypatch, conn)

    assert await TaskQueueRepo().postpone(9, reason="ещё раз") is False
    conn.execute.assert_not_awaited()
    conn.fetchval.assert_awaited_once_with(
        _FAIL_SQL, 9, "max_postpone_count", None
    )
