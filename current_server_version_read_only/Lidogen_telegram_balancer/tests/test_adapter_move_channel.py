"""D4 — unit- и integration-тесты move_channel: adapter + dispatch + PG."""
from __future__ import annotations

import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import Account, AccountsRepo, DualReserveResult
from app_balance.queue.adapter import ClumpTaskAdapter, execute_task
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.per_op_reading import TaskType, TaskTypesRepo
from app_balance.queue.resource_check import ResourceChecker
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import ClaimedTask, EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data
from tests.test_adapter import FakeClump, _account, _account_getter, _move_accounts, _move_claimed
from tests.test_dispatch import FakeTaskTypes, _dispatcher, _fake_queue
from tests.test_dispatch_dual_account import FakeDualAccounts, _move_task_type

_PREFIX = "test_d4_move_"
_TEST_PRIO = 2_000_000_000


async def _noop_sync_after_move(_task, _target, _clump) -> None:
    """Unit-тесты без PG dual-write."""
def _claimed_move_dispatch(
    task_id: int = 1,
    *,
    source_account_id: int = 10,
    target_account_id: int = 20,
    payload: dict | None = None,
) -> ClaimedTask:
    return ClaimedTask(
        id=task_id,
        task_type_id=11,
        task_type_code="move_channel",
        priority=100,
        payload=payload
        or {
            "parser_id": "p1",
            "channel_ref": "@move",
        },
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


class SessionNamedDualAccounts(FakeDualAccounts):
    """C4 fake с реальными session_name для проверки D4 account_getter."""

    def __init__(
        self,
        *,
        source_id: int = 10,
        target_id: int = 20,
        source_session: str = "/src",
        target_session: str = "/tgt",
        reserve_pair_ok: bool = True,
    ) -> None:
        super().__init__(reserve_pair_ok=reserve_pair_ok)
        self._accounts = {
            source_id: _account(source_id, source_session),
            target_id: _account(target_id, target_session),
        }

    async def reserve_pair(
        self, source_id: int, target_id: int, task_id: int
    ) -> DualReserveResult | None:
        result = await super().reserve_pair(source_id, target_id, task_id)
        if result is None:
            return None
        return DualReserveResult(
            source=self._accounts[source_id],
            target=self._accounts[target_id],
        )

    async def get_by_id(self, account_id: int) -> Account | None:
        return self._accounts.get(account_id)


class TrackingAccountGetter:
    def __init__(self, accounts: dict[int, Account]) -> None:
        self._accounts = accounts
        self.calls: list[int] = []

    async def __call__(self, account_id: int) -> Account | None:
        self.calls.append(account_id)
        return self._accounts.get(account_id)


# --- unit: execute_task — дополнительные инварианты D4 ---


@pytest.mark.asyncio
async def test_move_uses_task_account_ids_not_execute_account_param() -> None:
    """D4: session names берутся из source/target ID, а не из account dispatch."""
    clump = FakeClump()
    accounts = _move_accounts()
    task = _move_claimed(payload={"parser_id": "p1", "channel_ref": "@mv"})

    await execute_task(
        task,
        account=_account(20, "/wrong_target_from_dispatch"),
        clump_getter=lambda _pid: clump,
        account_getter=await _account_getter(accounts),
    )

    clump.move_channel.assert_awaited_once_with(
        "@mv",
        "/src",
        "/tgt",
        webhook_url=None,
    )


@pytest.mark.asyncio
async def test_move_source_account_not_found_raises() -> None:
    clump = FakeClump()
    getter = await _account_getter({20: _account(20, "/tgt")})

    with pytest.raises(RuntimeError, match="account_not_found:10"):
        await execute_task(
            _move_claimed(),
            account=_account(20, "/tgt"),
            clump_getter=lambda _pid: clump,
            account_getter=getter,
        )


@pytest.mark.asyncio
async def test_move_missing_source_account_id_raises() -> None:
    task = _move_claimed(source_account_id=None, target_account_id=20)

    with pytest.raises(ValueError, match="missing dual account ids"):
        await execute_task(
            task,
            account=_account(20, "/tgt"),
            clump_getter=lambda _pid: FakeClump(),
            account_getter=await _account_getter(_move_accounts()),
        )


@pytest.mark.asyncio
async def test_move_missing_parser_id_raises() -> None:
    task = _move_claimed(payload={"channel_ref": "@ch"})

    with pytest.raises(ValueError, match="missing parser_id"):
        await execute_task(
            task,
            account=_account(20, "/tgt"),
            clump_getter=lambda _pid: FakeClump(),
            account_getter=await _account_getter(_move_accounts()),
        )


@pytest.mark.asyncio
async def test_move_missing_channel_ref_raises() -> None:
    task = _move_claimed(payload={"parser_id": "p1"})

    with pytest.raises(ValueError, match="missing channel_ref"):
        await execute_task(
            task,
            account=_account(20, "/tgt"),
            clump_getter=lambda _pid: FakeClump(),
            account_getter=await _account_getter(_move_accounts()),
        )


@pytest.mark.asyncio
async def test_move_account_getter_called_for_both_ids() -> None:
    clump = FakeClump()
    accounts = _move_accounts()
    tracker = TrackingAccountGetter(accounts)

    await execute_task(
        _move_claimed(),
        account=_account(20, "/tgt"),
        clump_getter=lambda _pid: clump,
        account_getter=tracker,
    )

    assert tracker.calls == [10, 20]


@pytest.mark.asyncio
async def test_move_clump_start_failure_does_not_fail_task() -> None:
    clump = FakeClump()
    clump.start.side_effect = RuntimeError("start failed")

    await execute_task(
        _move_claimed(),
        account=_account(20, "/tgt"),
        clump_getter=lambda _pid: clump,
        account_getter=await _account_getter(_move_accounts()),
    )

    clump.move_channel.assert_awaited_once()
    clump.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_unsupported_task_type_raises() -> None:
    task = _move_claimed()
    task = ClaimedTask(
        id=task.id,
        task_type_id=99,
        task_type_code="unknown_type",
        priority=task.priority,
        payload=task.payload,
        channel_id=task.channel_id,
        account_id=task.account_id,
        source_account_id=task.source_account_id,
        target_account_id=task.target_account_id,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        dedup_key=task.dedup_key,
        locked_by=task.locked_by,
        locked_until=task.locked_until,
    )

    with pytest.raises(NotImplementedError, match="unknown_type"):
        await execute_task(
            task,
            account=_account(20, "/tgt"),
            clump_getter=lambda _pid: FakeClump(),
            account_getter=await _account_getter(_move_accounts()),
        )


# --- unit: dispatch + ClumpTaskAdapter (без PG) ---


@pytest.mark.asyncio
async def test_dispatch_move_channel_clump_adapter_happy_path() -> None:
    clump = FakeClump()
    accounts = SessionNamedDualAccounts()
    adapter = ClumpTaskAdapter(
        clump_getter=lambda _pid: clump,
        account_getter=accounts.get_by_id,
        sync_after_move=_noop_sync_after_move,
    )
    dispatcher = _dispatcher(
        _fake_queue(),
        accounts,
        FakeTaskTypes(_move_task_type()),
        adapter,
    )

    result = await dispatcher.dispatch(_claimed_move_dispatch(21))

    assert result == DispatchResult.COMPLETED
    clump.move_channel.assert_awaited_once_with(
        "@move",
        "/src",
        "/tgt",
        webhook_url=None,
    )
    clump.start.assert_awaited_once()
    assert accounts.released == [10, 20]


@pytest.mark.asyncio
async def test_dispatch_move_channel_clump_error_releases_both() -> None:
    clump = FakeClump()
    clump.move_channel.return_value = {
        "channel": "@move",
        "from_session": "/src",
        "to_session": "/tgt",
        "session_name": None,
        "chat_id": None,
        "error": "unexpected_owner",
    }
    accounts = SessionNamedDualAccounts()
    adapter = ClumpTaskAdapter(
        clump_getter=lambda _pid: clump,
        account_getter=accounts.get_by_id,
        sync_after_move=_noop_sync_after_move,
    )
    queue = _fake_queue()
    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(_move_task_type()),
        adapter,
    )

    result = await dispatcher.dispatch(_claimed_move_dispatch(22))

    assert result == DispatchResult.RETRIED
    assert accounts.released == [10, 20]
    assert queue.failed == [(22, "unexpected_owner")]


@pytest.mark.asyncio
async def test_dispatch_move_channel_missing_parser_id_retries() -> None:
    clump = FakeClump()
    accounts = SessionNamedDualAccounts()
    adapter = ClumpTaskAdapter(
        clump_getter=lambda _pid: clump,
        account_getter=accounts.get_by_id,
        sync_after_move=_noop_sync_after_move,
    )
    queue = _fake_queue()
    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(_move_task_type()),
        adapter,
    )

    result = await dispatcher.dispatch(
        _claimed_move_dispatch(23, payload={"channel_ref": "@move"})
    )

    assert result == DispatchResult.RETRIED
    assert clump.move_channel.await_count == 0
    assert accounts.released == [10, 20]
    assert queue.failed[0][1] == "missing parser_id"


# --- integration: dispatch + ClumpTaskAdapter + PG ---


@pytest.fixture
async def move_channel_adapter_ctx(pg_pool):
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
            payload={
                "parser_id": "p1",
                "channel_ref": "@pg_move",
                "webhook_url": "https://hook.example/move",
            },
        )
    )
    assert enqueue.created and enqueue.task_id is not None

    yield {
        "source_id": source_id,
        "target_id": target_id,
        "source_name": source_name,
        "target_name": target_name,
        "task_id": enqueue.task_id,
    }
    await _cleanup()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_move_channel_resolves_sessions_from_pg(
    move_channel_adapter_ctx,
) -> None:
    ctx = move_channel_adapter_ctx
    clump = FakeClump()
    accounts_repo = AccountsRepo()
    adapter = ClumpTaskAdapter(
        clump_getter=lambda _pid: clump,
        account_getter=accounts_repo.get_by_id,
    )

    repo = TaskQueueRepo()
    claimed = await repo.claim_by_id(ctx["task_id"], locked_by="d4-it")
    assert claimed is not None
    assert claimed.id == ctx["task_id"]

    usage = ResourceUsageRepo()
    dispatcher = TaskDispatcher(
        queue=repo,
        accounts=accounts_repo,
        task_types=TaskTypesRepo(),
        adapter=adapter,
        usage=usage,
        resource_check=ResourceChecker(usage),
        postpone_delay_seconds=300,
    )

    result = await dispatcher.dispatch(claimed)

    assert result == DispatchResult.COMPLETED
    clump.move_channel.assert_awaited_once_with(
        "@pg_move",
        ctx["source_name"],
        ctx["target_name"],
        webhook_url="https://hook.example/move",
    )

    async with db.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", ctx["task_id"]
        )
        source_busy = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", ctx["source_id"]
        )
        target_busy = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", ctx["target_id"]
        )

    assert status == "done"
    assert source_busy is None
    assert target_busy is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_move_channel_adapter_error_persists_retry(
    move_channel_adapter_ctx,
) -> None:
    ctx = move_channel_adapter_ctx
    clump = FakeClump()
    clump.move_channel.return_value = {
        "channel": "@pg_move",
        "from_session": ctx["source_name"],
        "to_session": ctx["target_name"],
        "session_name": None,
        "chat_id": None,
        "error": "telethon_flood",
    }
    accounts_repo = AccountsRepo()
    adapter = ClumpTaskAdapter(
        clump_getter=lambda _pid: clump,
        account_getter=accounts_repo.get_by_id,
    )

    repo = TaskQueueRepo()
    claimed = await repo.claim_by_id(ctx["task_id"], locked_by="d4-fail-it")
    assert claimed is not None

    usage = ResourceUsageRepo()
    dispatcher = TaskDispatcher(
        queue=repo,
        accounts=accounts_repo,
        task_types=TaskTypesRepo(),
        adapter=adapter,
        usage=usage,
        resource_check=ResourceChecker(usage),
        postpone_delay_seconds=300,
    )

    result = await dispatcher.dispatch(claimed)

    assert result == DispatchResult.RETRIED

    async with db.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", ctx["task_id"]
        )
        last_error = await conn.fetchval(
            "SELECT last_error FROM task_queue WHERE id = $1", ctx["task_id"]
        )
        source_busy = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", ctx["source_id"]
        )
        target_busy = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", ctx["target_id"]
        )
        usage_count = await conn.fetchval(
            "SELECT COUNT(*) FROM account_resource_usage WHERE task_id = $1",
            ctx["task_id"],
        )

    assert status == "retry"
    assert last_error == "telethon_flood"
    assert source_busy is None
    assert target_busy is None
    assert usage_count > 0
