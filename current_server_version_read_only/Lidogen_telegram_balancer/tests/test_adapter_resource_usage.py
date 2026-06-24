"""D5 — unit- и integration-тесты INSERT account_resource_usage при старте execute."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import Account, AccountsRepo
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.mock_adapter import MockTaskAdapter
from app_balance.queue.per_op_reading import TaskType, TaskTypeOp, TaskTypesRepo
from app_balance.queue.resource_check import ResourceCheckResult, ResourceChecker
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import ClaimedTask, EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data
from tests.test_dispatch import (
    FakeAccounts,
    FakeQueue,
    FakeResourceChecker,
    FakeTaskTypes,
    _account,
    _claimed,
    _dispatcher,
    _fake_queue,
    _task_type,
)

_PREFIX = "test_d5_"
_TEST_PRIO = 2_000_000_000


@dataclass(frozen=True, slots=True)
class UsageInsert:
    account_id: int
    op_type_id: int
    task_id: int
    task_type_id: int
    units: int
    task_attempt_id: int | None = None


class RecordingUsageRepo(ResourceUsageRepo):
    """In-memory mock: перехватывает insert без PG."""

    def __init__(self) -> None:
        self.inserts: list[UsageInsert] = []
        self._next_id = 1

    async def insert(
        self,
        account_id: int,
        op_type_id: int,
        task_id: int,
        task_type_id: int,
        units: int = 1,
        task_attempt_id: int | None = None,
    ) -> int:
        self.inserts.append(
            UsageInsert(
                account_id=account_id,
                op_type_id=op_type_id,
                task_id=task_id,
                task_type_id=task_type_id,
                units=units,
                task_attempt_id=task_attempt_id,
            )
        )
        row_id = self._next_id
        self._next_id += 1
        return row_id


def _op(
    *,
    op_type_id: int,
    op_code: str,
    units_per_execution: int = 1,
    account_role: str = "primary",
    op_is_enabled: bool = True,
) -> TaskTypeOp:
    return TaskTypeOp(
        task_type_op_id=op_type_id,
        op_type_id=op_type_id,
        op_code=op_code,
        op_name=op_code,
        units_per_execution=units_per_execution,
        account_role=account_role,  # type: ignore[arg-type]
        rph_limit=100,
        reserve_percent=Decimal("10"),
        op_is_enabled=op_is_enabled,
    )


def _task_type_with_ops(
    ops: tuple[TaskTypeOp, ...],
    *,
    code: str = "parser_add_channel",
    task_type_id: int = 10,
    uses_two_accounts: bool = False,
) -> TaskType:
    return TaskType(
        id=task_type_id,
        code=code,
        name=code,
        description=None,
        is_enabled=True,
        default_priority=500,
        min_available_resource_percent=80,
        requires_specific_account=False,
        uses_two_accounts=uses_two_accounts,
        max_attempts=5,
        retry_delay_seconds=60,
        retry_backoff_multiplier=Decimal("2"),
        max_retry_delay_seconds=1800,
        target_queue_size=None,
        max_postpone_count=100,
        task_timeout_seconds=600,
        created_at=None,
        updated_at=None,
        ops=ops,
    )


def _parser_add_channel_ops() -> tuple[TaskTypeOp, ...]:
    return (
        _op(op_type_id=1, op_code="get_entity", units_per_execution=2),
        _op(op_type_id=2, op_code="channels.JoinChannel", units_per_execution=2),
        _op(op_type_id=3, op_code="channels.GetFullChannel", units_per_execution=1),
        _op(op_type_id=4, op_code="channels.GetParticipant", units_per_execution=1),
    )


@pytest.mark.asyncio
async def test_record_primary_ops_parser_add_channel() -> None:
    usage = RecordingUsageRepo()
    task_type = _task_type_with_ops(_parser_add_channel_ops())

    ids = await usage.record_for_task(
        task_type=task_type,
        task_id=100,
        accounts_by_role={"primary": 42},
    )

    assert len(ids) == 4
    assert len(usage.inserts) == 4
    assert all(row.account_id == 42 for row in usage.inserts)
    assert all(row.task_id == 100 for row in usage.inserts)
    units_by_op = {row.op_type_id: row.units for row in usage.inserts}
    assert units_by_op == {1: 2, 2: 2, 3: 1, 4: 1}


@pytest.mark.asyncio
async def test_record_skips_disabled_ops() -> None:
    usage = RecordingUsageRepo()
    ops = (
        _op(op_type_id=1, op_code="get_entity"),
        _op(op_type_id=2, op_code="join", op_is_enabled=False),
    )
    task_type = _task_type_with_ops(ops)

    await usage.record_for_task(
        task_type=task_type,
        task_id=1,
        accounts_by_role={"primary": 5},
    )

    assert len(usage.inserts) == 1
    assert usage.inserts[0].op_type_id == 1


@pytest.mark.asyncio
async def test_record_move_channel_source_and_target() -> None:
    usage = RecordingUsageRepo()
    ops = (
        _op(op_type_id=10, op_code="channels.GetParticipant", account_role="source"),
        _op(op_type_id=20, op_code="get_entity", account_role="target", units_per_execution=2),
        _op(op_type_id=21, op_code="channels.JoinChannel", account_role="target"),
    )
    task_type = _task_type_with_ops(
        ops, code="move_channel", task_type_id=11, uses_two_accounts=True
    )

    await usage.record_for_task(
        task_type=task_type,
        task_id=7,
        accounts_by_role={"source": 100, "target": 200},
    )

    assert len(usage.inserts) == 3
    by_role = {
        row.op_type_id: row.account_id
        for row in usage.inserts
    }
    assert by_role[10] == 100
    assert by_role[20] == 200
    assert by_role[21] == 200


@pytest.mark.asyncio
async def test_record_raises_on_missing_role_account() -> None:
    usage = RecordingUsageRepo()
    task_type = _task_type_with_ops(
        (_op(op_type_id=1, op_code="get_entity", account_role="source"),),
        uses_two_accounts=True,
    )

    with pytest.raises(ValueError, match="no account for role 'source'"):
        await usage.record_for_task(
            task_type=task_type,
            task_id=1,
            accounts_by_role={"primary": 1},
        )


class OrderTrackingAdapter(MockTaskAdapter):
    def __init__(self, usage: RecordingUsageRepo) -> None:
        super().__init__()
        self._usage = usage

    async def execute(self, task: ClaimedTask, *, account: Account) -> None:
        task_inserts = [row for row in self._usage.inserts if row.task_id == task.id]
        if not task_inserts:
            raise AssertionError("usage must be recorded before adapter.execute")
        await super().execute(task, account=account)


@pytest.mark.asyncio
async def test_dispatch_records_usage_before_adapter() -> None:
    usage = RecordingUsageRepo()
    adapter = OrderTrackingAdapter(usage)
    ops = _parser_add_channel_ops()
    dispatcher = _dispatcher(
        _fake_queue(),
        FakeAccounts(),
        FakeTaskTypes(_task_type_with_ops(ops)),
        adapter,
        usage=usage,
    )

    result = await dispatcher.dispatch(_claimed(55))

    assert result == DispatchResult.COMPLETED
    assert len(usage.inserts) == 4
    assert len(adapter.executions) == 1


@pytest.mark.asyncio
async def test_dispatch_adapter_error_usage_not_rolled_back() -> None:
    usage = RecordingUsageRepo()
    ops = (_op(op_type_id=1, op_code="get_entity"),)

    class BoomAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise RuntimeError("boom")

    dispatcher = _dispatcher(
        _fake_queue(),
        FakeAccounts(),
        FakeTaskTypes(_task_type_with_ops(ops)),
        BoomAdapter(),
        usage=usage,
    )

    result = await dispatcher.dispatch(_claimed(56))

    assert result == DispatchResult.RETRIED
    assert len(usage.inserts) == 1
    assert usage.inserts[0].task_id == 56


@pytest.mark.asyncio
async def test_dispatch_postpone_no_usage() -> None:
    usage = RecordingUsageRepo()
    ops = (_op(op_type_id=1, op_code="get_entity"),)
    checker = FakeResourceChecker(fail_accounts={42})
    dispatcher = _dispatcher(
        _fake_queue(),
        FakeAccounts(),
        FakeTaskTypes(_task_type_with_ops(ops)),
        MockTaskAdapter(),
        resource_check=checker,
        usage=usage,
    )

    result = await dispatcher.dispatch(_claimed(57, account_id=42))

    assert result == DispatchResult.POSTPONED
    assert usage.inserts == []


@pytest.fixture
async def d5_ctx(pg_pool):
    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_PREFIX}%",
            session_name_like=f"{_PREFIX}%",
        )

    await _cleanup()

    session_name = f"{_PREFIX}{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )

    dedup_key = f"{_PREFIX}{uuid.uuid4().hex}"
    enqueue = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=dedup_key,
            priority=_TEST_PRIO,
            account_id=account_id,
        )
    )
    assert enqueue.created and enqueue.task_id is not None

    task_types = TaskTypesRepo()
    task_type = await task_types.get_by_code("parser_add_channel")
    assert task_type is not None

    yield {
        "account_id": account_id,
        "task_id": enqueue.task_id,
        "task_type": task_type,
        "dedup_key": dedup_key,
    }
    await _cleanup()


def _expected_primary_usage(task_type: TaskType) -> dict[int, int]:
    expected: dict[int, int] = {}
    for op in task_type.ops:
        if op.account_role != "primary" or not op.op_is_enabled:
            continue
        expected[op.op_type_id] = (
            expected.get(op.op_type_id, 0) + op.units_per_execution
        )
    return expected


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_parser_add_channel_writes_usage(d5_ctx) -> None:
    ctx = d5_ctx
    repo = TaskQueueRepo()
    claimed = await repo.claim_by_id(ctx["task_id"], locked_by="d5-it")
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

    expected = _expected_primary_usage(ctx["task_type"])
    async with db.acquire() as conn:
        row_count = await conn.fetchval(
            "SELECT COUNT(*) FROM account_resource_usage WHERE task_id = $1",
            ctx["task_id"],
        )
        assert row_count == len(expected)

    for op_type_id, units in expected.items():
        counted = await usage.count_last_hour(ctx["account_id"], op_type_id)
        assert counted == units


@pytest.fixture
async def d5_move_ctx(pg_pool):
    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_PREFIX}move_%",
            session_name_like=f"{_PREFIX}move_%",
        )

    await _cleanup()

    source_name = f"{_PREFIX}move_src_{uuid.uuid4().hex}"
    target_name = f"{_PREFIX}move_tgt_{uuid.uuid4().hex}"
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

    dedup_key = f"{_PREFIX}move_{uuid.uuid4().hex}"
    enqueue = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="move_channel",
            dedup_key=dedup_key,
            priority=_TEST_PRIO,
            source_account_id=source_id,
            target_account_id=target_id,
        )
    )
    assert enqueue.created and enqueue.task_id is not None

    task_types = TaskTypesRepo()
    task_type = await task_types.get_by_code("move_channel")
    assert task_type is not None

    yield {
        "source_id": source_id,
        "target_id": target_id,
        "task_id": enqueue.task_id,
        "task_type": task_type,
    }
    await _cleanup()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_move_channel_dual_usage(d5_move_ctx) -> None:
    ctx = d5_move_ctx
    repo = TaskQueueRepo()
    claimed = await repo.claim_by_id(ctx["task_id"], locked_by="d5-move-it")
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

    source_ops = [
        op for op in ctx["task_type"].ops if op.account_role == "source" and op.op_is_enabled
    ]
    target_ops = [
        op for op in ctx["task_type"].ops if op.account_role == "target" and op.op_is_enabled
    ]
    assert source_ops and target_ops

    async with db.acquire() as conn:
        source_count = await conn.fetchval(
            "SELECT COUNT(*) FROM account_resource_usage "
            "WHERE task_id = $1 AND account_id = $2",
            ctx["task_id"],
            ctx["source_id"],
        )
        target_count = await conn.fetchval(
            "SELECT COUNT(*) FROM account_resource_usage "
            "WHERE task_id = $1 AND account_id = $2",
            ctx["task_id"],
            ctx["target_id"],
        )

    assert source_count == len(source_ops)
    assert target_count == len(target_ops)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_execute_failure_usage_persists(d5_ctx) -> None:
    ctx = d5_ctx
    repo = TaskQueueRepo()
    claimed = await repo.claim_by_id(ctx["task_id"], locked_by="d5-fail-it")
    assert claimed is not None

    class RaisingAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise RuntimeError("telethon failed")

    usage = ResourceUsageRepo()
    dispatcher = TaskDispatcher(
        queue=repo,
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=RaisingAdapter(),
        usage=usage,
        resource_check=ResourceChecker(usage),
        postpone_delay_seconds=300,
    )

    result = await dispatcher.dispatch(claimed)

    assert result == DispatchResult.RETRIED

    async with db.acquire() as conn:
        usage_count = await conn.fetchval(
            "SELECT COUNT(*) FROM account_resource_usage WHERE task_id = $1",
            ctx["task_id"],
        )
        attempt_count = await conn.fetchval(
            "SELECT attempt_count FROM task_queue WHERE id = $1",
            ctx["task_id"],
        )

    assert usage_count > 0
    assert attempt_count == 1
