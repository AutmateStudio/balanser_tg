"""C2 — unit-тесты TaskDispatcher (без PG)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from app_balance.queue.accounts import Account
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.mock_adapter import MockTaskAdapter
from app_balance.queue.per_op_reading import TaskType
from app_balance.queue.resource_check import ResourceCheckResult
from app_balance.queue.task_queue import ClaimedTask


def _claimed(
    task_id: int = 1,
    *,
    account_id: int | None = None,
    task_type_code: str = "parser_add_channel",
) -> ClaimedTask:
    return ClaimedTask(
        id=task_id,
        task_type_id=10,
        task_type_code=task_type_code,
        priority=500,
        payload={"ref": "@test"},
        channel_id=None,
        account_id=account_id,
        source_account_id=None,
        target_account_id=None,
        attempt_count=1,
        max_attempts=5,
        dedup_key=None,
        locked_by="w",
        locked_until=None,
    )


def _task_type(
    *,
    code: str = "parser_add_channel",
    uses_two_accounts: bool = False,
    is_enabled: bool = True,
) -> TaskType:
    return TaskType(
        id=10,
        code=code,
        name=code,
        description=None,
        is_enabled=is_enabled,
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
        ops=(),
    )


def _account(account_id: int = 42) -> Account:
    return Account(
        id=account_id,
        session_name=f"sess_{account_id}",
        status="active",
        is_enabled=True,
        current_task_id=1,
        cooldown_until=None,
        last_used_at=None,
    )


@dataclass
class FakeQueue:
    completed: list[int]
    postponed: list[tuple[int, int, str | None]]
    failed: list[tuple[int, str | None]]
    assigned: list[tuple[int, int]]
    complete_raises: Exception | None = None

    def __post_init__(self) -> None:
        if self.completed is ...:
            pass

    async def complete(self, task_id: int) -> bool:
        if self.complete_raises:
            raise self.complete_raises
        self.completed.append(task_id)
        return True

    async def postpone(
        self, task_id: int, delay_seconds: int = 300, reason: str | None = None
    ) -> None:
        self.postponed.append((task_id, delay_seconds, reason))

    async def reschedule_or_fail(
        self, task_id: int, error: str | None, retry_delay_seconds: int
    ) -> str:
        self.failed.append((task_id, error))
        return "retry"

    async def assign_account(self, task_id: int, account_id: int) -> None:
        self.assigned.append((task_id, account_id))

    async def begin_execution_attempt(self, task_id: int) -> int:
        return 1


def _fake_queue() -> FakeQueue:
    return FakeQueue(completed=[], postponed=[], failed=[], assigned=[])


class FakeAccounts:
    _UNSET = object()

    def __init__(
        self,
        *,
        pick_result: Account | None | object = _UNSET,
        pick_results: list[Account | None] | None = None,
        reserve_ok: bool = True,
    ) -> None:
        if pick_results is not None:
            self._pick_results = list(pick_results)
        elif pick_result is FakeAccounts._UNSET:
            self._pick_results = [_account()]
        else:
            self._pick_results = [pick_result]  # type: ignore[list-item]
        self.reserve_ok = reserve_ok
        self.released: list[int] = []
        self.reserved: list[tuple[int, int]] = []
        self.pick_calls: list[int] = []

    async def pick_and_reserve(
        self,
        task_id: int,
        *,
        exclude_account_ids: frozenset[int] | None = None,
    ) -> Account | None:
        self.pick_calls.append(task_id)
        exclude = exclude_account_ids or frozenset()
        if not self._pick_results:
            return None
        if len(self._pick_results) == 1:
            candidate = self._pick_results[0]
            if candidate is None or candidate.id in exclude:
                return None
            return candidate
        while self._pick_results:
            candidate = self._pick_results.pop(0)
            if candidate is None:
                return None
            if candidate.id not in exclude:
                return candidate
        return None

    async def reserve(self, account_id: int, task_id: int) -> bool:
        self.reserved.append((account_id, task_id))
        return self.reserve_ok

    async def get_by_id(self, account_id: int) -> Account | None:
        return _account(account_id)

    async def release(self, account_id: int) -> None:
        self.released.append(account_id)


class FakeResourceChecker:
    def __init__(
        self,
        *,
        default_ok: bool = True,
        fail_accounts: set[int] | None = None,
        failing_op_code: str = "get_entity",
    ) -> None:
        self.default_ok = default_ok
        self.fail_accounts = fail_accounts or set()
        self.failing_op_code = failing_op_code
        self.checked: list[tuple[int, str]] = []

    async def check_account(self, account_id: int, task_type: TaskType, **kwargs):
        role = kwargs.get("account_role", "primary")
        self.checked.append((account_id, role))
        if account_id in self.fail_accounts or not self.default_ok:
            return ResourceCheckResult(
                ok=False,
                threshold=task_type.min_available_resource_percent,
                failing_op_code=self.failing_op_code,
                available_percent=79.0,
                account_id=account_id,
                reason_code="insufficient_resource",
            )
        return ResourceCheckResult(
            ok=True,
            threshold=task_type.min_available_resource_percent,
            account_id=account_id,
        )


class FakeTaskTypes:
    def __init__(self, task_type: TaskType | None) -> None:
        self._task_type = task_type

    async def get_by_code(self, code: str) -> TaskType | None:
        if self._task_type is None:
            return None
        if self._task_type.code != code:
            return None
        return self._task_type


def _dispatcher(
    queue: FakeQueue,
    accounts: FakeAccounts,
    task_types: FakeTaskTypes,
    adapter: MockTaskAdapter | None = None,
    resource_check: FakeResourceChecker | None = None,
    usage=None,
) -> TaskDispatcher:
    from tests.test_adapter_resource_usage import RecordingUsageRepo

    usage_repo = usage or RecordingUsageRepo()
    return TaskDispatcher(
        queue=queue,
        accounts=accounts,
        task_types=task_types,
        adapter=adapter or MockTaskAdapter(),
        usage=usage_repo,
        resource_check=resource_check or FakeResourceChecker(),
        postpone_delay_seconds=300,
        retry_delay_seconds=60,
    )


@pytest.mark.asyncio
async def test_happy_path_pick_execute_complete_release() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()
    adapter = MockTaskAdapter()
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), adapter
    )

    result = await dispatcher.dispatch(_claimed(7))

    assert result == DispatchResult.COMPLETED
    assert len(adapter.executions) == 1
    assert adapter.executions[0].task_id == 7
    assert adapter.executions[0].session_name == "sess_42"
    assert queue.completed == [7]
    assert queue.assigned == [(7, 42)]
    assert accounts.released == [42]
    assert queue.postponed == []


@pytest.mark.asyncio
async def test_fixed_account_id_uses_reserve_not_pick() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()
    dispatcher = _dispatcher(queue, accounts, FakeTaskTypes(_task_type()))

    result = await dispatcher.dispatch(_claimed(8, account_id=99))

    assert result == DispatchResult.COMPLETED
    assert accounts.reserved == [(99, 8)]
    assert accounts.pick_calls == []
    assert accounts.released == [99]


@pytest.mark.asyncio
async def test_fixed_account_id_reserve_fail_postpones() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts(reserve_ok=False)
    adapter = MockTaskAdapter()
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), adapter
    )

    result = await dispatcher.dispatch(_claimed(9, account_id=99))

    assert result == DispatchResult.POSTPONED
    assert queue.postponed == [(9, 300, "account_reserve_failed:99")]
    assert adapter.executions == []
    assert accounts.released == []


@pytest.mark.asyncio
async def test_pick_and_reserve_none_postpones() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts(pick_result=None)
    adapter = MockTaskAdapter()
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), adapter
    )

    result = await dispatcher.dispatch(_claimed(10))

    assert result == DispatchResult.POSTPONED
    assert queue.postponed == [(10, 300, "no_available_account")]
    assert adapter.executions == []
    assert accounts.released == []


@pytest.mark.asyncio
async def test_adapter_error_reschedules_and_releases() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()

    class BoomAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise RuntimeError("boom")

    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), BoomAdapter()
    )

    result = await dispatcher.dispatch(_claimed(12))

    assert result == DispatchResult.RETRIED
    assert queue.failed == [(12, "boom")]
    assert queue.completed == []
    assert accounts.released == [42]


@pytest.mark.asyncio
async def test_complete_failure_still_releases() -> None:
    queue = _fake_queue()
    queue.complete_raises = RuntimeError("complete failed")
    accounts = FakeAccounts()
    dispatcher = _dispatcher(queue, accounts, FakeTaskTypes(_task_type()))

    result = await dispatcher.dispatch(_claimed(13))

    assert result == DispatchResult.RETRIED
    assert accounts.released == [42]


@pytest.mark.asyncio
async def test_unknown_task_type_fails() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()
    dispatcher = _dispatcher(queue, accounts, FakeTaskTypes(None))

    result = await dispatcher.dispatch(_claimed(14))

    assert result == DispatchResult.RETRIED
    assert queue.failed[0][1] == "unknown_task_type:parser_add_channel"
    assert accounts.released == []


@pytest.mark.asyncio
async def test_resource_check_fail_postpones_and_releases() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts(pick_results=[_account(42), None])
    adapter = MockTaskAdapter()
    checker = FakeResourceChecker(default_ok=False)
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), adapter, checker
    )

    result = await dispatcher.dispatch(_claimed(15))

    assert result == DispatchResult.POSTPONED
    assert queue.postponed == [(15, 300, "insufficient_resource:42:get_entity")]
    assert adapter.executions == []
    assert accounts.released == [42]
    assert accounts.pick_calls == [15, 15]


@pytest.mark.asyncio
async def test_auto_pick_skips_low_resource_account() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts(
        pick_results=[_account(1), _account(2), None],
    )
    adapter = MockTaskAdapter()
    checker = FakeResourceChecker(fail_accounts={1})
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), adapter, checker
    )

    result = await dispatcher.dispatch(_claimed(16))

    assert result == DispatchResult.COMPLETED
    assert accounts.pick_calls == [16, 16]
    assert accounts.released == [1, 2]
    assert queue.assigned == [(16, 2)]
    assert adapter.executions[0].session_name == "sess_2"


@pytest.mark.asyncio
async def test_all_accounts_low_resource_postpones() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts(pick_results=[_account(1), None])
    adapter = MockTaskAdapter()
    checker = FakeResourceChecker(fail_accounts={1})
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), adapter, checker
    )

    result = await dispatcher.dispatch(_claimed(17))

    assert result == DispatchResult.POSTPONED
    assert queue.postponed == [(17, 300, "insufficient_resource:1:get_entity")]
    assert adapter.executions == []
    assert accounts.released == [1]


@pytest.mark.asyncio
async def test_auto_pick_single_account_low_resource_postpones_without_loop() -> None:
    """Один аккаунт с низким ресурсом: exclude после reject → postpone, не зависание."""
    queue = _fake_queue()
    accounts = FakeAccounts(pick_results=[_account(1)])
    adapter = MockTaskAdapter()
    checker = FakeResourceChecker(fail_accounts={1})
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), adapter, checker
    )

    result = await dispatcher.dispatch(_claimed(19))

    assert result == DispatchResult.POSTPONED
    assert queue.postponed == [(19, 300, "insufficient_resource:1:get_entity")]
    assert adapter.executions == []
    assert accounts.released == [1]
    assert accounts.pick_calls == [19, 19]


@pytest.mark.asyncio
async def test_fixed_account_low_resource_postpones() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()
    adapter = MockTaskAdapter()
    checker = FakeResourceChecker(fail_accounts={99})
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), adapter, checker
    )

    result = await dispatcher.dispatch(_claimed(18, account_id=99))

    assert result == DispatchResult.POSTPONED
    assert queue.postponed == [
        (18, 300, "insufficient_resource:99:get_entity"),
    ]
    assert adapter.executions == []
    assert accounts.released == [99]
