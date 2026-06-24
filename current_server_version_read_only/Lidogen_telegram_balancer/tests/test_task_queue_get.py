"""D10 — интеграционные тесты TaskQueueRepo.get_by_id."""
from __future__ import annotations

import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg

_DEDUP_PREFIX = "test_d10_"


def _unique_key() -> str:
    return f"{_DEDUP_PREFIX}{uuid.uuid4().hex}"


@pytest.fixture
async def clean_queue(pg_pool):
    async def _cleanup() -> None:
        async with db.acquire() as conn:
            await conn.execute(
                "DELETE FROM task_queue WHERE dedup_key LIKE $1",
                f"{_DEDUP_PREFIX}%",
            )

    await _cleanup()
    yield
    await _cleanup()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_by_id_after_enqueue(clean_queue) -> None:
    repo = TaskQueueRepo()
    payload = {
        "parser_id": "p1",
        "channel_ref": "@chan",
        "action_id": "act-123",
    }
    enqueued = await repo.enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            payload=payload,
            dedup_key=_unique_key(),
            created_by="test_d10",
        )
    )
    assert enqueued.created is True
    assert enqueued.task_id is not None

    snapshot = await repo.get_by_id(enqueued.task_id)
    assert snapshot is not None
    assert snapshot.id == enqueued.task_id
    assert snapshot.task_type_code == "parser_add_channel"
    assert snapshot.status == "queued"
    assert snapshot.attempt_count == 0
    assert snapshot.postpone_count == 0
    assert snapshot.last_error is None
    assert snapshot.payload == payload
    assert snapshot.started_at is None
    assert snapshot.finished_at is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_by_id_missing_returns_none(clean_queue) -> None:
    snapshot = await TaskQueueRepo().get_by_id(9_999_999_999)
    assert snapshot is None
