"""D7 — unit-тесты dual-write hook-ов в adapter (без PG)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app_balance.queue.adapter import execute_task
from app_balance.queue.errors import RetryableError
from app_balance.queue.task_queue import ClaimedTask
from tests.test_adapter import (
    FakeClump,
    _account,
    _account_getter,
    _claimed,
    _move_claimed,
    _move_accounts,
)


def _claimed_with_channel(*, channel_id: int = 500) -> ClaimedTask:
    task = _claimed(
        payload={
            "parser_id": "p1",
            "channel_ref": "@ch",
        },
    )
    return ClaimedTask(
        id=task.id,
        task_type_id=task.task_type_id,
        task_type_code=task.task_type_code,
        priority=task.priority,
        payload=task.payload,
        channel_id=channel_id,
        account_id=task.account_id,
        source_account_id=task.source_account_id,
        target_account_id=task.target_account_id,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        dedup_key=task.dedup_key,
        locked_by=task.locked_by,
        locked_until=task.locked_until,
    )


def _move_claimed_with_channel(*, channel_id: int = 600) -> ClaimedTask:
    task = _move_claimed()
    return ClaimedTask(
        id=task.id,
        task_type_id=task.task_type_id,
        task_type_code=task.task_type_code,
        priority=task.priority,
        payload=task.payload,
        channel_id=channel_id,
        account_id=task.account_id,
        source_account_id=task.source_account_id,
        target_account_id=task.target_account_id,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        dedup_key=task.dedup_key,
        locked_by=task.locked_by,
        locked_until=task.locked_until,
    )


@pytest.mark.asyncio
async def test_add_channel_sync_called_on_success() -> None:
    clump = FakeClump()
    sync_after_add = AsyncMock()
    sync_after_move = AsyncMock()
    task = _claimed_with_channel()

    await execute_task(
        task,
        account=_account(),
        clump_getter=lambda _pid: clump,
        sync_after_add=sync_after_add,
        sync_after_move=sync_after_move,
    )

    sync_after_add.assert_awaited_once_with(task, _account(), clump)
    sync_after_move.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_channel_sync_not_called_on_clump_error() -> None:
    clump = FakeClump()
    clump.add_channel_on_session.return_value = {
        "channel": "@ch",
        "session_name": "/s1",
        "chat_id": None,
        "error": "join_failed",
    }
    sync_after_add = AsyncMock()

    with pytest.raises(RetryableError, match="join_failed"):
        await execute_task(
            _claimed_with_channel(),
            account=_account(),
            clump_getter=lambda _pid: clump,
            sync_after_add=sync_after_add,
        )

    sync_after_add.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_channel_sync_called_on_success() -> None:
    clump = FakeClump()
    sync_after_add = AsyncMock()
    sync_after_move = AsyncMock()
    task = _move_claimed_with_channel()
    accounts = _move_accounts()

    await execute_task(
        task,
        account=accounts[20],
        clump_getter=lambda _pid: clump,
        account_getter=await _account_getter(accounts),
        sync_after_add=sync_after_add,
        sync_after_move=sync_after_move,
    )

    sync_after_move.assert_awaited_once_with(task, accounts[20], clump)
    sync_after_add.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_channel_sync_not_called_on_clump_error() -> None:
    clump = FakeClump()
    clump.move_channel.return_value = {
        "channel": "@ch",
        "from_session": "/src",
        "to_session": "/tgt",
        "session_name": None,
        "chat_id": None,
        "error": "move_failed",
    }
    sync_after_move = AsyncMock()
    accounts = _move_accounts()

    with pytest.raises(RetryableError, match="move_failed"):
        await execute_task(
            _move_claimed_with_channel(),
            account=accounts[20],
            clump_getter=lambda _pid: clump,
            account_getter=await _account_getter(accounts),
            sync_after_move=sync_after_move,
        )

    sync_after_move.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_channel_sync_failure_propagates() -> None:
    clump = FakeClump()
    sync_after_add = AsyncMock(side_effect=RuntimeError("source_channel_not_found:500"))

    with pytest.raises(RuntimeError, match="source_channel_not_found:500"):
        await execute_task(
            _claimed_with_channel(),
            account=_account(),
            clump_getter=lambda _pid: clump,
            sync_after_add=sync_after_add,
        )
