"""Unit-тесты TaskQueueRepo.enqueue_many / find_fatal_history_batch (без PG)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app_balance.queue.error_codes import ErrorCode
from app_balance.queue.task_queue import (
    EnqueueInput,
    FatalHistoryInfo,
    TaskQueueRepo,
    UnknownTaskTypeError,
)


class _FakeTaskType:
    def __init__(self) -> None:
        self.id = 1
        self.code = "parser_add_channel"
        self.is_enabled = True
        self.default_priority = 500
        self.max_attempts = 3


@pytest.mark.asyncio
async def test_find_fatal_history_batch_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = AsyncMock()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": 10,
                "dedup_key": "k-fatal",
                "status": "failed",
                "last_error": f"{ErrorCode.CHANNEL_PRIVATE}:x",
            },
            {
                "id": 11,
                "dedup_key": "k-flood",
                "status": "failed",
                "last_error": "flood_wait:30",
            },
            {
                "id": 12,
                "dedup_key": "k-done",
                "status": "done",
                "last_error": None,
            },
        ]
    )

    @asynccontextmanager
    async def _acquire():
        yield conn

    monkeypatch.setattr("app_balance.queue.task_queue.acquire", _acquire)
    repo = TaskQueueRepo()
    result = await repo.find_fatal_history_batch(
        ["k-fatal", "k-flood", "k-done", "k-fatal"]
    )

    assert set(result.keys()) == {"k-fatal"}
    assert result["k-fatal"].task_id == 10
    assert result["k-fatal"].error_code == ErrorCode.CHANNEL_PRIVATE
    conn.fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_enqueue_many_creates_and_reports_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = AsyncMock()
    # INSERT RETURNING — только один создан, второй conflict
    conn.fetch = AsyncMock(
        side_effect=[
            [{"id": 100, "dedup_key": "k1"}],  # INSERT
            [{"id": 55, "dedup_key": "k2"}],  # active lookup for missing
        ]
    )

    @asynccontextmanager
    async def _acquire():
        yield conn

    monkeypatch.setattr("app_balance.queue.task_queue.acquire", _acquire)

    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_FakeTaskType())
    repo = TaskQueueRepo(task_types=task_types)
    repo.find_fatal_history_batch = AsyncMock(return_value={})

    items = [
        EnqueueInput(
            task_type_code="parser_add_channel",
            payload={"channel_ref": "@a"},
            dedup_key="k1",
            created_by="test",
        ),
        EnqueueInput(
            task_type_code="parser_add_channel",
            payload={"channel_ref": "@b"},
            dedup_key="k2",
            created_by="test",
        ),
    ]
    results = await repo.enqueue_many(items, skip_known_fatal=True)

    assert len(results) == 2
    assert results[0].created is True and results[0].task_id == 100
    assert results[1].created is False and results[1].existing_task_id == 55
    # get_by_code один раз на уникальный код
    assert task_types.get_by_code.await_count == 1
    repo.find_fatal_history_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_enqueue_many_skips_fatal_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = AsyncMock()
    conn.fetch = AsyncMock(
        return_value=[{"id": 200, "dedup_key": "k-ok"}]
    )

    @asynccontextmanager
    async def _acquire():
        yield conn

    monkeypatch.setattr("app_balance.queue.task_queue.acquire", _acquire)

    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_FakeTaskType())
    repo = TaskQueueRepo(task_types=task_types)
    repo.find_fatal_history_batch = AsyncMock(
        return_value={
            "k-dead": FatalHistoryInfo(
                task_id=9,
                error_code=ErrorCode.INVALID_PAYLOAD,
                last_error="invalid_payload",
            )
        }
    )

    items = [
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key="k-dead",
            payload={"channel_ref": "@dead"},
        ),
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key="k-ok",
            payload={"channel_ref": "@ok"},
        ),
    ]
    results = await repo.enqueue_many(items, skip_known_fatal=True)

    assert results[0].skipped_reason == "fatal_history"
    assert results[0].fatal_error_code == ErrorCode.INVALID_PAYLOAD
    assert results[0].existing_task_id == 9
    assert results[1].created is True and results[1].task_id == 200
    # INSERT только для k-ok (один row в UNNEST)
    insert_args = conn.fetch.await_args_list[0].args
    dedup_keys_arg = insert_args[9]  # $9 = dedup_keys
    assert dedup_keys_arg == ["k-ok"]


@pytest.mark.asyncio
async def test_enqueue_many_unknown_type_raises() -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=None)
    repo = TaskQueueRepo(task_types=task_types)
    with pytest.raises(UnknownTaskTypeError):
        await repo.enqueue_many(
            [
                EnqueueInput(
                    task_type_code="no_such",
                    dedup_key="k",
                )
            ]
        )


@pytest.mark.asyncio
async def test_enqueue_many_empty() -> None:
    repo = TaskQueueRepo(task_types=AsyncMock())
    assert await repo.enqueue_many([]) == []
