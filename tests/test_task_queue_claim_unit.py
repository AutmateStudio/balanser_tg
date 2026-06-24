"""B4 — юнит-тесты claim_next / finalize без PostgreSQL."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app_balance.queue.task_queue import (
    ACTIVE_STATUSES,
    CLAIMABLE_STATUSES,
    ClaimedTask,
    TaskQueueRepo,
    _BEGIN_EXECUTION_ATTEMPT_SQL,
    _CLAIM_NEXT_SQL,
    _COMPLETE_SQL,
    _FAIL_SQL,
    _RESCHEDULE_OR_FAIL_SQL,
    _row_to_claimed,
)


def _sample_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": 42,
        "task_type_id": 1,
        "task_type_code": "parser_add_channel",
        "priority": 500,
        "payload": {"channel_ref": "@x"},
        "channel_id": None,
        "account_id": 7,
        "source_account_id": None,
        "target_account_id": None,
        "attempt_count": 1,
        "max_attempts": 3,
        "dedup_key": "k1",
        "locked_by": "worker-a",
        "locked_until": datetime(2026, 6, 17, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


def test_row_to_claimed_parses_json_string_payload() -> None:
    row = _sample_row(payload=json.dumps({"a": 1}))
    claimed = _row_to_claimed(row)
    assert claimed.payload == {"a": 1}
    assert claimed.id == 42
    assert claimed.task_type_code == "parser_add_channel"


def test_row_to_claimed_handles_dict_payload_and_empty() -> None:
    claimed = _row_to_claimed(_sample_row(payload={"x": 2}))
    assert claimed.payload == {"x": 2}

    empty = _row_to_claimed(_sample_row(payload=None))
    assert empty.payload == {}


def test_claimable_statuses_exclude_in_progress() -> None:
    """in_progress не входит в выборку claim — только queued/scheduled/retry."""
    assert "in_progress" in ACTIVE_STATUSES
    assert "in_progress" not in CLAIMABLE_STATUSES
    assert set(CLAIMABLE_STATUSES) == {"queued", "scheduled", "retry"}


def test_claim_sql_uses_max_priority_and_random() -> None:
    sql = _CLAIM_NEXT_SQL.lower()
    assert "max_prio" in sql
    assert "max(priority)" in sql
    assert "order by random()" in sql
    assert "created_at asc" not in sql
    assert "for update skip locked" in sql


def test_claim_sql_does_not_increment_attempt_count() -> None:
    set_clause = _CLAIM_NEXT_SQL.split("SET", 1)[1].split("FROM", 1)[0].lower()
    assert "attempt_count" not in set_clause


def test_begin_execution_attempt_sql_increments() -> None:
    sql = _BEGIN_EXECUTION_ATTEMPT_SQL.lower()
    assert "attempt_count = attempt_count + 1" in sql


def test_complete_and_reschedule_sql_present() -> None:
    assert "status = 'done'" in _COMPLETE_SQL
    assert "locked_until = null" in _COMPLETE_SQL.lower()
    assert "'retry'" in _RESCHEDULE_OR_FAIL_SQL
    assert "'failed'" in _RESCHEDULE_OR_FAIL_SQL


@pytest.mark.asyncio
async def test_claim_next_returns_none_when_queue_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)

    @asynccontextmanager
    async def fake_acquire():
        yield conn

    monkeypatch.setattr("app_balance.queue.task_queue.acquire", fake_acquire)

    result = await TaskQueueRepo().claim_next("worker-x", lock_ttl_seconds=120)
    assert result is None
    conn.fetchrow.assert_awaited_once()
    args = conn.fetchrow.await_args.args
    assert args[0] is _CLAIM_NEXT_SQL
    assert args[1] == "worker-x"
    assert args[2] is None
    assert args[3] == 120


@pytest.mark.asyncio
async def test_claim_next_passes_task_type_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=_sample_row())

    @asynccontextmanager
    async def fake_acquire():
        yield conn

    monkeypatch.setattr("app_balance.queue.task_queue.acquire", fake_acquire)

    codes = ["parser_add_channel", "move_channel"]
    result = await TaskQueueRepo().claim_next("w", task_type_codes=codes)

    assert isinstance(result, ClaimedTask)
    assert result.id == 42
    assert conn.fetchrow.await_args.args[2] == codes


@pytest.mark.asyncio
async def test_complete_executes_update(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")

    @asynccontextmanager
    async def fake_acquire():
        yield conn

    monkeypatch.setattr("app_balance.queue.task_queue.acquire", fake_acquire)

    assert await TaskQueueRepo().complete(99) is True
    conn.execute.assert_awaited_once_with(_COMPLETE_SQL, 99)


@pytest.mark.asyncio
async def test_reschedule_or_fail_returns_status(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value="retry")

    @asynccontextmanager
    async def fake_acquire():
        yield conn

    monkeypatch.setattr("app_balance.queue.task_queue.acquire", fake_acquire)

    status = await TaskQueueRepo().reschedule_or_fail(5, "boom", retry_delay_seconds=30)
    assert status == "retry"
    conn.fetchval.assert_awaited_once_with(
        _RESCHEDULE_OR_FAIL_SQL, 5, "boom", 30
    )


@pytest.mark.asyncio
async def test_fail_returns_status(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value="failed")

    @asynccontextmanager
    async def fake_acquire():
        yield conn

    monkeypatch.setattr("app_balance.queue.task_queue.acquire", fake_acquire)

    status = await TaskQueueRepo().fail(7, "invalid_payload")
    assert status == "failed"
    conn.fetchval.assert_awaited_once_with(_FAIL_SQL, 7, "invalid_payload")
