"""C4 — dual reserve move_channel (source + target)."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import Account, AccountsRepo, DualReserveResult
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.mock_adapter import MockTaskAdapter
from app_balance.queue.per_op_reading import TaskType, TaskTypesRepo
from app_balance.queue.resource_check import ResourceCheckResult, ResourceChecker
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import ClaimedTask, EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data
from tests.test_dispatch import (
    FakeResourceChecker,
    FakeTaskTypes,
    _account,
    _dispatcher,
    _fake_queue,
)

_PREFIX = "test_c4_dual_"
_TEST_PRIO = 2_000_000_000


def _move_task_type() -> TaskType:
    return TaskType(
        id=11,
        code="move_channel",
        name="move_channel",
        description=None,
        is_enabled=True,
        default_priority=100,
        min_available_resource_percent=80,
        requires_specific_account=False,
        uses_two_accounts=True,
        max_attempts=5,
        retry_delay_seconds=60,
        retry_backoff_multiplier=Decimal("2"),
        max_retry_delay_seconds=1800,
        target_queue_size=20,
        max_postpone_count=100,
        task_timeout_seconds=600,
        created_at=None,
        updated_at=None,
        ops=(),
    )


def _claimed_move(
    task_id: int = 1,
    *,
    source_account_id: int | None = 10,
    target_account_id: int | None = 20,
) -> ClaimedTask:
    return ClaimedTask(
        id=task_id,
        task_type_id=11,
        task_type_code="move_channel",
        priority=100,
        payload={"ref": "@move"},
        channel_id=555,
        account_id=None,
        source_account_id=source_account_id,
        target_account_id=target_account_id,
        attempt_count=0,
        max_attempts=5,
        dedup_key=None,
        locked_by="w",
        locked_until=None,
    )


class FakeDualAccounts:
    def __init__(self, *, reserve_pair_ok: bool = True) -> None:
        self.reserve_pair_ok = reserve_pair_ok
        self.reserve_pair_calls: list[tuple[int, int, int]] = []
        self.released: list[int] = []

    async def pick_and_reserve(
        self, task_id: int, *, exclude_account_ids: frozenset[int] | None = None
    ) -> Account | None:
        raise AssertionError("pick_and_reserve не используется для move_channel")

    async def reserve(self, account_id: int, task_id: int) -> bool:
        raise AssertionError("reserve не используется для move_channel")

    async def reserve_pair(
        self, source_id: int, target_id: int, task_id: int
    ) -> DualReserveResult | None:
        self.reserve_pair_calls.append((source_id, target_id, task_id))
        if not self.reserve_pair_ok:
            return None
        return DualReserveResult(
            source=_account(source_id),
            target=_account(target_id),
        )

    async def get_by_id(self, account_id: int) -> Account | None:
        return _account(account_id)

    async def release(self, account_id: int, task_id: int | None = None) -> None:
        self.released.append(account_id)


@pytest.mark.asyncio
async def test_move_channel_happy_path() -> None:
    queue = _fake_queue()
    accounts = FakeDualAccounts()
    adapter = MockTaskAdapter()
    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(_move_task_type()),
        adapter,
    )

    result = await dispatcher.dispatch(_claimed_move(7, source_account_id=10, target_account_id=20))

    assert result == DispatchResult.COMPLETED
    assert accounts.reserve_pair_calls == [(10, 20, 7)]
    assert accounts.released == [10, 20]
    assert queue.completed == [7]
    assert len(adapter.executions) == 1
    assert adapter.executions[0].session_name == "sess_20"
    assert queue.assigned == []


@pytest.mark.asyncio
async def test_move_channel_missing_ids_postpones() -> None:
    queue = _fake_queue()
    accounts = FakeDualAccounts()
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_move_task_type()), MockTaskAdapter()
    )

    result = await dispatcher.dispatch(
        _claimed_move(8, source_account_id=None, target_account_id=20)
    )

    assert result == DispatchResult.POSTPONED
    assert queue.postponed == [(8, 300, "missing_dual_accounts")]
    assert accounts.reserve_pair_calls == []
    assert accounts.released == []


@pytest.mark.asyncio
async def test_move_channel_same_id_postpones() -> None:
    queue = _fake_queue()
    accounts = FakeDualAccounts()
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_move_task_type()), MockTaskAdapter()
    )

    result = await dispatcher.dispatch(
        _claimed_move(9, source_account_id=5, target_account_id=5)
    )

    assert result == DispatchResult.POSTPONED
    assert queue.postponed == [(9, 300, "dual_accounts_same_id")]
    assert accounts.reserve_pair_calls == []


@pytest.mark.asyncio
async def test_move_channel_source_resource_fail() -> None:
    queue = _fake_queue()
    accounts = FakeDualAccounts()
    checker = FakeResourceChecker(fail_accounts={10})
    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(_move_task_type()),
        MockTaskAdapter(),
        checker,
    )

    result = await dispatcher.dispatch(_claimed_move(10))

    assert result == DispatchResult.POSTPONED
    assert queue.postponed[0][2] == "insufficient_resource:10:get_entity"
    assert accounts.reserve_pair_calls == []
    assert (10, "source") in checker.checked
    assert accounts.released == []


@pytest.mark.asyncio
async def test_move_channel_target_resource_fail() -> None:
    queue = _fake_queue()
    accounts = FakeDualAccounts()
    checker = FakeResourceChecker(fail_accounts={20})
    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(_move_task_type()),
        MockTaskAdapter(),
        checker,
    )

    result = await dispatcher.dispatch(_claimed_move(11))

    assert result == DispatchResult.POSTPONED
    assert queue.postponed[0][2] == "insufficient_resource:20:get_entity"
    assert accounts.reserve_pair_calls == []
    assert (10, "source") in checker.checked
    assert (20, "target") in checker.checked


@pytest.mark.asyncio
async def test_move_channel_reserve_pair_fail() -> None:
    queue = _fake_queue()
    accounts = FakeDualAccounts(reserve_pair_ok=False)
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_move_task_type()), MockTaskAdapter()
    )

    result = await dispatcher.dispatch(_claimed_move(12))

    assert result == DispatchResult.POSTPONED
    assert queue.postponed == [(12, 300, "dual_account_reserve_failed:10:20")]
    assert accounts.released == []


@pytest.mark.asyncio
async def test_move_channel_adapter_error_releases_both() -> None:
    queue = _fake_queue()
    accounts = FakeDualAccounts()

    class BoomAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise RuntimeError("boom")

    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(_move_task_type()),
        BoomAdapter(),
    )

    result = await dispatcher.dispatch(_claimed_move(13))

    assert result == DispatchResult.RETRIED
    assert accounts.released == [10, 20]


@pytest.fixture
async def dual_accounts_and_task(pg_pool):
    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_PREFIX}%",
            session_name_like=f"{_PREFIX}%",
        )

    await _cleanup()

    source_name = f"{_PREFIX}src_{uuid.uuid4().hex}"
    target_name = f"{_PREFIX}tgt_{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        source_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            source_name,
        )
        target_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            target_name,
        )

    dedup_key = f"{_PREFIX}{uuid.uuid4().hex}"
    enqueue = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="move_channel",
            dedup_key=dedup_key,
            priority=_TEST_PRIO,
            source_account_id=source_id,
            target_account_id=target_id,
            channel_id=None,
        )
    )
    assert enqueue.created and enqueue.task_id is not None

    yield {
        "source_id": source_id,
        "target_id": target_id,
        "task_id": enqueue.task_id,
    }
    await _cleanup()


async def _current_task(account_id: int) -> int | None:
    async with db.acquire() as conn:
        return await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", account_id
        )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_reserve_pair_sets_both_current_task_id(dual_accounts_and_task) -> None:
    ctx = dual_accounts_and_task
    repo = AccountsRepo()

    dual = await repo.reserve_pair(ctx["source_id"], ctx["target_id"], ctx["task_id"])

    assert dual is not None
    assert await _current_task(ctx["source_id"]) == ctx["task_id"]
    assert await _current_task(ctx["target_id"]) == ctx["task_id"]

    await repo.release(ctx["source_id"])
    await repo.release(ctx["target_id"])


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_reserve_pair_rolls_back_when_target_busy(dual_accounts_and_task) -> None:
    ctx = dual_accounts_and_task
    repo = AccountsRepo()

    other_key = f"{_PREFIX}other_{uuid.uuid4().hex}"
    other = await TaskQueueRepo().enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=other_key)
    )
    assert other.task_id is not None
    assert await repo.reserve(ctx["target_id"], other.task_id) is True

    dual = await repo.reserve_pair(ctx["source_id"], ctx["target_id"], ctx["task_id"])

    assert dual is None
    assert await _current_task(ctx["source_id"]) is None
    assert await _current_task(ctx["target_id"]) == other.task_id

    await repo.release(ctx["target_id"])


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_move_channel_integration(dual_accounts_and_task) -> None:
    ctx = dual_accounts_and_task
    repo = TaskQueueRepo()
    claimed = await repo.claim_by_id(ctx["task_id"], locked_by="c4-it")
    assert claimed is not None
    assert claimed.id == ctx["task_id"]

    usage = ResourceUsageRepo()
    dispatcher = TaskDispatcher(
        queue=repo,
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=MockTaskAdapter(),
        usage=usage,
        resource_check=ResourceChecker(usage),
        postpone_delay_seconds=300,
    )

    result = await dispatcher.dispatch(claimed)

    assert result == DispatchResult.COMPLETED
    assert await _current_task(ctx["source_id"]) is None
    assert await _current_task(ctx["target_id"]) is None

    async with db.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", ctx["task_id"]
        )
    assert status == "done"
