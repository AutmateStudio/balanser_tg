"""B5 — юнит-тесты postpone без PostgreSQL."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app_balance.queue.task_queue import TaskQueueRepo, _POSTPONE_SQL


def test_postpone_sql_sets_scheduled_and_clears_lock() -> None:
    sql = _POSTPONE_SQL.lower()
    assert "status = 'scheduled'" in sql
    assert "postpone_count = postpone_count + 1" in sql
    assert "locked_by = null" in sql
    assert "locked_at = null" in sql
    assert "locked_until = null" in sql
    assert "attempt_count" not in sql


@pytest.mark.asyncio
async def test_postpone_executes_update(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")

    @asynccontextmanager
    async def fake_acquire():
        yield conn

    monkeypatch.setattr("app_balance.queue.task_queue.acquire", fake_acquire)

    assert await TaskQueueRepo().postpone(42, delay_seconds=120, reason="нет аккаунта") is True
    conn.execute.assert_awaited_once_with(
        _POSTPONE_SQL, 42, 120, "нет аккаунта"
    )


@pytest.mark.asyncio
async def test_postpone_default_delay_and_no_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")

    @asynccontextmanager
    async def fake_acquire():
        yield conn

    monkeypatch.setattr("app_balance.queue.task_queue.acquire", fake_acquire)

    assert await TaskQueueRepo().postpone(7) is True
    conn.execute.assert_awaited_once_with(_POSTPONE_SQL, 7, 300, None)
