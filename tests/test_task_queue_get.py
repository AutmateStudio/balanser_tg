"""D10 / E5 — интеграционные тесты TaskQueueRepo.get_by_id и last_error_code."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.dispatch import TaskDispatcher
from app_balance.queue.errors import RetryableError
from app_balance.queue.mock_adapter import MockTaskAdapter
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.task_attempts import TaskAttemptsRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg, TEST_ISOLATION_PRIORITY
from tests.pg_cleanup import cleanup_queue_test_data
from tests.queue_integration_helpers import (
    AlwaysOkResourceChecker,
    enqueue_isolated_task,
    insert_test_account,
    require_claimed_task,
)
_DEDUP_PREFIX = "test_d10_"


def _unique_key() -> str:
    return f"{_DEDUP_PREFIX}{uuid.uuid4().hex}"


@pytest.fixture
async def clean_queue(pg_pool):
    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_DEDUP_PREFIX}%",
            session_name_like=f"{_DEDUP_PREFIX}%",
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
    # run_after в будущем — фоновый worker не claim'ит задачу (run_after <= now()).
    run_after = datetime.now(timezone.utc) + timedelta(days=365)
    enqueued = await repo.enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            payload=payload,
            dedup_key=_unique_key(),
            priority=TEST_ISOLATION_PRIORITY,
            run_after=run_after,
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
    assert snapshot.last_error_code is None
    assert snapshot.payload == payload
    assert snapshot.started_at is None
    assert snapshot.finished_at is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_by_id_missing_returns_none(clean_queue) -> None:
    snapshot = await TaskQueueRepo().get_by_id(9_999_999_999)
    assert snapshot is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_by_id_last_error_code_after_retry(clean_queue) -> None:
    """E5/D10: last_error_code — стабильный префикс last_error для мониторинга."""
    account_id, _ = await insert_test_account(prefix=_DEDUP_PREFIX)
    task_id = await enqueue_isolated_task(
        prefix=_DEDUP_PREFIX,
        task_type_code="parser_add_channel",
        account_id=account_id,
        payload={"ref": "@d10_err"},
    )
    claimed = await require_claimed_task(
        task_id, locked_by=f"{_DEDUP_PREFIX}worker"
    )

    class FloodAdapter(MockTaskAdapter):
        async def execute(self, task, *, account) -> None:  # type: ignore[override]
            raise RetryableError("flood_wait", "FloodWait 30s", retry_after_seconds=30)

    repo = TaskQueueRepo()
    dispatcher = TaskDispatcher(
        queue=repo,
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=FloodAdapter(),
        resource_check=AlwaysOkResourceChecker(),
        attempts=TaskAttemptsRepo(),
        retry_delay_seconds=60,
    )
    await dispatcher.dispatch(claimed)

    snapshot = await repo.get_by_id(task_id)
    assert snapshot is not None
    assert snapshot.status == "retry"
    assert snapshot.last_error == "flood_wait"
    assert snapshot.last_error_code == "flood_wait"
