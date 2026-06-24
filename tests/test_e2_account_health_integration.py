"""E2 — integration: flood → cooldown, ban → banned в PG; pick исключает cooldown (D6)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.error_codes import ErrorCode
from app_balance.queue.errors import PermanentError, RetryableError
from app_balance.queue.mock_adapter import MockTaskAdapter
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.task_attempts import TaskAttemptsRepo
from app_balance.queue.task_queue import TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data
from tests.queue_integration_helpers import (
    AlwaysOkResourceChecker,
    enqueue_isolated_task,
    insert_test_account,
    require_claimed_task,
)

_PREFIX = "test_e2_"
_TASK_TYPE = "parser_add_channel"


@pytest.fixture
async def e2_clean(pg_pool):
    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_PREFIX}%",
            session_name_like=f"{_PREFIX}%",
        )

    await _cleanup()
    yield
    await _cleanup()


def _build_dispatcher(adapter: MockTaskAdapter) -> TaskDispatcher:
    return TaskDispatcher(
        queue=TaskQueueRepo(),
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=adapter,
        resource_check=AlwaysOkResourceChecker(),
        attempts=TaskAttemptsRepo(),
        postpone_delay_seconds=300,
        retry_delay_seconds=60,
    )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2_flood_wait_persists_cooldown_and_excludes_from_pick(e2_clean) -> None:
    cooled_id, _ = await insert_test_account(prefix=f"{_PREFIX}cooled_")
    other_id, _ = await insert_test_account(prefix=f"{_PREFIX}other_")
    task_id = await enqueue_isolated_task(
        prefix=_PREFIX,
        task_type_code=_TASK_TYPE,
        account_id=cooled_id,
    )
    claimed = await require_claimed_task(task_id, locked_by=f"{_PREFIX}flood")

    class FloodAdapter(MockTaskAdapter):
        async def execute(self, task, *, account) -> None:  # type: ignore[override]
            raise RetryableError(
                ErrorCode.FLOOD_WAIT,
                "FloodWait 120s",
                retry_after_seconds=120,
            )

    result = await _build_dispatcher(FloodAdapter()).dispatch(claimed)
    assert result == DispatchResult.RETRIED

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, cooldown_until FROM accounts WHERE id = $1",
            cooled_id,
        )
    assert row is not None
    assert row["status"] == "cooldown"
    assert row["cooldown_until"] is not None
    assert row["cooldown_until"] > datetime.now(timezone.utc)

    accounts = AccountsRepo()
    picked = await accounts.pick_and_reserve(task_id + 10_000)
    assert picked is not None
    assert picked.id == other_id
    await accounts.release(picked.id)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2_ban_marks_account_banned(e2_clean) -> None:
    account_id, session_name = await insert_test_account(prefix=f"{_PREFIX}ban_")
    task_id = await enqueue_isolated_task(
        prefix=_PREFIX,
        task_type_code=_TASK_TYPE,
        account_id=account_id,
    )
    claimed = await require_claimed_task(task_id, locked_by=f"{_PREFIX}ban")

    class BanAdapter(MockTaskAdapter):
        async def execute(self, task, *, account) -> None:  # type: ignore[override]
            raise PermanentError(ErrorCode.BANNED, "UserDeactivated")

    result = await _build_dispatcher(BanAdapter()).dispatch(claimed)
    assert result == DispatchResult.FAILED

    async with db.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM accounts WHERE session_name = $1",
            session_name,
        )
        task_status = await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", task_id
        )
    assert status == "banned"
    assert task_status == "failed"
