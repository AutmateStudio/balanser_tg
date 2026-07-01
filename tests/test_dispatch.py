"""C2 — unit-тесты TaskDispatcher (без PG)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app_balance.queue.accounts import Account
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.error_codes import ErrorCode
from app_balance.queue.errors import (
    PermanentError,
    ResourceError,
    RetryableError,
)
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
    retry_delay_seconds: int = 60,
    retry_backoff_multiplier: Decimal = Decimal("2"),
    max_retry_delay_seconds: int = 1800,
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
        retry_delay_seconds=retry_delay_seconds,
        retry_backoff_multiplier=retry_backoff_multiplier,
        max_retry_delay_seconds=max_retry_delay_seconds,
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
    permanent_failed: list[tuple[int, str | None]] = field(default_factory=list)
    retry_delays: list[int] = field(default_factory=list)
    begin_calls: list[int] = field(default_factory=list)
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
    ) -> bool:
        self.postponed.append((task_id, delay_seconds, reason))
        return True

    async def reschedule_or_fail(
        self, task_id: int, error: str | None, retry_delay_seconds: int
    ) -> str:
        self.failed.append((task_id, error))
        self.retry_delays.append(retry_delay_seconds)
        return "retry"

    async def fail(self, task_id: int, error: str | None = None) -> str | None:
        self.permanent_failed.append((task_id, error))
        return "failed"

    async def assign_account(self, task_id: int, account_id: int) -> None:
        self.assigned.append((task_id, account_id))

    async def begin_execution_attempt(self, task_id: int) -> int:
        self.begin_calls.append(task_id)
        return len(self.begin_calls)


def _fake_queue() -> FakeQueue:
    return FakeQueue(completed=[], postponed=[], failed=[], assigned=[])


class FakeAttempts:
    """B9 — in-memory mock TaskAttemptsRepo без PG."""

    def __init__(self) -> None:
        self.inserted: list[dict] = []
        self.finished: list[dict] = []
        self._next_id = 1

    async def insert(
        self,
        *,
        task_id: int,
        task_type_id: int,
        account_id: int,
        attempt_number: int,
        source_account_id: int | None = None,
        target_account_id: int | None = None,
        started_at=None,
    ) -> int:
        attempt_id = self._next_id
        self._next_id += 1
        self.inserted.append(
            {
                "id": attempt_id,
                "task_id": task_id,
                "task_type_id": task_type_id,
                "account_id": account_id,
                "attempt_number": attempt_number,
                "source_account_id": source_account_id,
                "target_account_id": target_account_id,
            }
        )
        return attempt_id

    async def finish(
        self,
        attempt_id: int,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        finished_at=None,
    ) -> bool:
        if getattr(self, "finish_raises", False):
            raise RuntimeError("task_attempts finish failed")
        self.finished.append(
            {
                "id": attempt_id,
                "status": status,
                "error_code": error_code,
                "error_message": error_message,
            }
        )
        return True


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
        self.cooldowns: list[tuple[str, datetime]] = []
        self.bans: list[tuple[str, str | None]] = []
        self.account_errors: list[tuple[str, str | None]] = []

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

    async def release(self, account_id: int, task_id: int | None = None) -> None:
        self.released.append(account_id)

    async def set_cooldown(self, session_name: str, until: datetime) -> bool:
        self.cooldowns.append((session_name, until))
        return True

    async def set_banned(self, session_name: str, *, reason: str | None = None) -> bool:
        self.bans.append((session_name, reason))
        return True

    async def set_account_error(
        self, session_name: str, *, reason: str | None = None
    ) -> bool:
        self.account_errors.append((session_name, reason))
        return True


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
    attempts: FakeAttempts | None = None,
) -> TaskDispatcher:
    from tests.test_adapter_resource_usage import RecordingUsageRepo

    usage_repo = usage or RecordingUsageRepo()
    return TaskDispatcher(
        queue=queue,
        accounts=accounts,
        task_types=task_types,
        adapter=adapter or MockTaskAdapter(),
        usage=usage_repo,
        attempts=attempts or FakeAttempts(),
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


# --- E1: typed errors ---


@pytest.mark.asyncio
async def test_e1_retryable_error_reschedules() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()
    attempts = FakeAttempts()

    class RetryAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise RetryableError("clump_not_loaded", "clump_not_loaded:p1")

    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(_task_type()),
        RetryAdapter(),
        attempts=attempts,
    )

    result = await dispatcher.dispatch(_claimed(30, account_id=99))

    assert result == DispatchResult.RETRIED
    assert queue.failed == [(30, "clump_not_loaded")]
    assert queue.permanent_failed == []
    assert queue.postponed == []
    assert queue.retry_delays == [60]
    assert accounts.released == [99]
    assert attempts.finished == [
        {
            "id": 1,
            "status": "error",
            "error_code": "clump_not_loaded",
            "error_message": "clump_not_loaded:p1",
        }
    ]


@pytest.mark.asyncio
async def test_e1_retryable_error_uses_retry_after_seconds() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()

    class FloodAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise RetryableError(
                "flood_wait",
                "FloodWait 42s",
                retry_after_seconds=42,
            )

    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), FloodAdapter()
    )

    result = await dispatcher.dispatch(_claimed(31, account_id=99))

    assert result == DispatchResult.RETRIED
    assert queue.retry_delays == [42]


@pytest.mark.asyncio
async def test_e3_retry_backoff_increases_delay() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()

    class FailingAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise RetryableError("temporary", "temporary")

    task_type = _task_type(
        retry_delay_seconds=10,
        retry_backoff_multiplier=Decimal("2"),
        max_retry_delay_seconds=1800,
    )
    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(task_type),
        adapter=FailingAdapter(),
    )
    task = _claimed(50, account_id=99)

    result1 = await dispatcher.dispatch(task)
    result2 = await dispatcher.dispatch(task)

    assert result1 == DispatchResult.RETRIED
    assert result2 == DispatchResult.RETRIED
    assert queue.retry_delays == [10, 20]


@pytest.mark.asyncio
async def test_e3_retry_backoff_caps_max_delay() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()

    class FailingAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise RetryableError("temporary", "temporary")

    task_type = _task_type(
        retry_delay_seconds=10,
        retry_backoff_multiplier=Decimal("3"),
        max_retry_delay_seconds=15,
    )
    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(task_type),
        adapter=FailingAdapter(),
    )
    task = _claimed(51, account_id=99)

    await dispatcher.dispatch(task)
    await dispatcher.dispatch(task)

    assert queue.retry_delays == [10, 15]


@pytest.mark.asyncio
async def test_e1_permanent_error_fails_immediately() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()
    attempts = FakeAttempts()

    class PermanentAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise PermanentError("invalid_payload", "missing parser_id")

    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(_task_type()),
        PermanentAdapter(),
        attempts=attempts,
    )

    result = await dispatcher.dispatch(_claimed(32, account_id=99))

    assert result == DispatchResult.FAILED
    assert queue.permanent_failed == [(32, "invalid_payload")]
    assert queue.failed == []
    assert queue.postponed == []
    assert accounts.released == [99]
    assert attempts.finished[0]["error_code"] == "invalid_payload"


@pytest.mark.asyncio
async def test_e1_resource_error_postpones() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()
    attempts = FakeAttempts()

    class ResourceAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise ResourceError(
                "insufficient_resource",
                account_id=42,
                op_code="get_entity",
            )

    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(_task_type()),
        ResourceAdapter(),
        attempts=attempts,
    )

    result = await dispatcher.dispatch(_claimed(33, account_id=99))

    assert result == DispatchResult.POSTPONED
    assert queue.postponed == [
        (33, 300, "insufficient_resource:42:get_entity"),
    ]
    assert queue.failed == []
    assert queue.permanent_failed == []
    assert accounts.released == [99]
    assert len(attempts.inserted) == 1


@pytest.mark.asyncio
async def test_e2_flood_wait_sets_cooldown() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()

    class FloodAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise RetryableError(
                ErrorCode.FLOOD_WAIT,
                "FloodWait 30s",
                retry_after_seconds=30,
            )

    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), FloodAdapter()
    )

    result = await dispatcher.dispatch(_claimed(40, account_id=99))

    assert result == DispatchResult.RETRIED
    assert accounts.cooldowns
    session_name, until = accounts.cooldowns[0]
    assert session_name == "sess_99"
    assert until > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_e2_ban_sets_banned() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()

    class BanAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise PermanentError(ErrorCode.BANNED, "UserDeactivated")

    dispatcher = _dispatcher(queue, accounts, FakeTaskTypes(_task_type()), BanAdapter())

    result = await dispatcher.dispatch(_claimed(41, account_id=99))

    assert result == DispatchResult.FAILED
    assert accounts.bans == [("sess_99", "UserDeactivated")]


@pytest.mark.asyncio
async def test_e2_unauthorized_notifies_session(monkeypatch) -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()
    notified: list[tuple[str, str]] = []

    async def _notify(session_name: str, message: str) -> None:
        notified.append((session_name, message))

    monkeypatch.setattr(
        "discovery_api.session_registry.notify_session_unauthorized",
        _notify,
    )

    class UnauthorizedAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise PermanentError(
                ErrorCode.ACCOUNT_UNAUTHORIZED,
                "Сессия '/app/sessions/test4' не авторизована",
            )

    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), UnauthorizedAdapter()
    )

    result = await dispatcher.dispatch(_claimed(42, account_id=99))

    assert result == DispatchResult.FAILED
    assert notified == [
        ("sess_99", "Сессия '/app/sessions/test4' не авторизована"),
    ]


@pytest.mark.asyncio
async def test_e5_untyped_exception_writes_stable_code() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()
    attempts = FakeAttempts()

    class BoomAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise ValueError("boom: detail")

    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(_task_type()),
        BoomAdapter(),
        attempts=attempts,
    )

    result = await dispatcher.dispatch(_claimed(34, account_id=99))

    assert result == DispatchResult.RETRIED
    assert queue.failed == [(34, "unexpected_error")]
    assert queue.permanent_failed == []
    assert attempts.finished[0]["error_code"] == "unexpected_error"
    assert attempts.finished[0]["error_message"] == "boom: detail"


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
    assert queue.failed == [(12, "unexpected_error")]
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
    attempts = FakeAttempts()
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(None), attempts=attempts
    )

    result = await dispatcher.dispatch(_claimed(14))

    assert result == DispatchResult.RETRIED
    assert queue.failed[0][1] == "unknown_task_type:parser_add_channel"
    assert accounts.released == []
    assert attempts.inserted == []
    assert attempts.finished == []
    assert queue.begin_calls == []


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


# --- B9: task_attempts history ---


@pytest.mark.asyncio
async def test_b9_success_records_attempt() -> None:
    queue = _fake_queue()
    accounts = FakeAccounts()
    attempts = FakeAttempts()
    dispatcher = _dispatcher(
        queue, accounts, FakeTaskTypes(_task_type()), attempts=attempts
    )

    result = await dispatcher.dispatch(_claimed(20, account_id=99))

    assert result == DispatchResult.COMPLETED
    assert len(attempts.inserted) == 1
    rec = attempts.inserted[0]
    assert rec["task_id"] == 20
    assert rec["task_type_id"] == 10
    assert rec["account_id"] == 99
    assert rec["attempt_number"] == 1
    assert attempts.finished == [
        {"id": 1, "status": "success", "error_code": None, "error_message": None}
    ]


@pytest.mark.asyncio
async def test_b9_attempt_number_from_begin_execution() -> None:
    queue = _fake_queue()

    async def _begin(task_id: int) -> int:
        return 3

    queue.begin_execution_attempt = _begin  # type: ignore[assignment]
    attempts = FakeAttempts()
    dispatcher = _dispatcher(
        queue, FakeAccounts(), FakeTaskTypes(_task_type()), attempts=attempts
    )

    await dispatcher.dispatch(_claimed(21, account_id=99))

    assert attempts.inserted[0]["attempt_number"] == 3


@pytest.mark.asyncio
async def test_b9_error_finishes_attempt_with_code() -> None:
    queue = _fake_queue()
    attempts = FakeAttempts()

    class BoomAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise RuntimeError("clump_not_loaded:p1")

    dispatcher = _dispatcher(
        queue,
        FakeAccounts(),
        FakeTaskTypes(_task_type()),
        BoomAdapter(),
        attempts=attempts,
    )

    result = await dispatcher.dispatch(_claimed(22, account_id=99))

    assert result == DispatchResult.RETRIED
    assert len(attempts.inserted) == 1
    assert attempts.finished == [
        {
            "id": 1,
            "status": "error",
            "error_code": "unexpected_error",
            "error_message": "clump_not_loaded:p1",
        }
    ]


@pytest.mark.asyncio
async def test_b9_timeout_classified_as_timeout() -> None:
    queue = _fake_queue()
    attempts = FakeAttempts()

    class TimeoutAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise TimeoutError("slow")

    dispatcher = _dispatcher(
        queue,
        FakeAccounts(),
        FakeTaskTypes(_task_type()),
        TimeoutAdapter(),
        attempts=attempts,
    )

    result = await dispatcher.dispatch(_claimed(23, account_id=99))

    assert result == DispatchResult.RETRIED
    assert attempts.finished[0]["status"] == "timeout"
    assert attempts.finished[0]["error_code"] == "transient_error"
    assert queue.failed == [(23, "transient_error")]


@pytest.mark.asyncio
async def test_b9_no_attempt_when_postponed() -> None:
    queue = _fake_queue()
    attempts = FakeAttempts()
    checker = FakeResourceChecker(fail_accounts={99})
    dispatcher = _dispatcher(
        queue,
        FakeAccounts(),
        FakeTaskTypes(_task_type()),
        MockTaskAdapter(),
        resource_check=checker,
        attempts=attempts,
    )

    result = await dispatcher.dispatch(_claimed(24, account_id=99))

    assert result == DispatchResult.POSTPONED
    assert attempts.inserted == []
    assert attempts.finished == []


@pytest.mark.asyncio
async def test_b9_finish_failure_does_not_block_dispatch() -> None:
    """B9: сбой finish task_attempts не ломает retry задачи."""
    queue = _fake_queue()
    attempts = FakeAttempts()
    attempts.finish_raises = True

    class BoomAdapter(MockTaskAdapter):
        async def execute(self, task, *, account) -> None:  # type: ignore[override]
            raise RetryableError("transient_error", "temporary")

    dispatcher = _dispatcher(
        queue,
        FakeAccounts(),
        FakeTaskTypes(_task_type()),
        BoomAdapter(),
        attempts=attempts,
    )

    result = await dispatcher.dispatch(_claimed(27, account_id=99))

    assert result == DispatchResult.RETRIED
    assert len(attempts.inserted) == 1
    assert queue.retry_delays


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_code"),
    [
        (TimeoutError("slow"), "timeout", "transient_error"),
        (RetryableError("flood_wait"), "error", "flood_wait"),
        (PermanentError("invalid_payload"), "error", "invalid_payload"),
        (RuntimeError("boom"), "error", "unexpected_error"),
    ],
)
def test_classify_attempt_error_mapping(
    exc: Exception, expected_status: str, expected_code: str
) -> None:
    status, code = TaskDispatcher._classify_attempt_error(exc)
    assert status == expected_status
    assert code == expected_code


@pytest.mark.asyncio
async def test_b9_dual_account_attempt_records_roles() -> None:
    queue = _fake_queue()
    attempts = FakeAttempts()

    class FakeDualAccounts(FakeAccounts):
        async def reserve_pair(self, source_id, target_id, task_id):
            from app_balance.queue.accounts import DualReserveResult

            return DualReserveResult(
                source=_account(source_id), target=_account(target_id)
            )

    task = ClaimedTask(
        id=25,
        task_type_id=11,
        task_type_code="move_channel",
        priority=100,
        payload={"ref": "@m"},
        channel_id=None,
        account_id=None,
        source_account_id=100,
        target_account_id=200,
        attempt_count=1,
        max_attempts=5,
        dedup_key=None,
        locked_by="w",
        locked_until=None,
    )
    dispatcher = _dispatcher(
        queue,
        FakeDualAccounts(),
        FakeTaskTypes(_task_type(code="move_channel", uses_two_accounts=True)),
        attempts=attempts,
    )

    result = await dispatcher.dispatch(task)

    assert result == DispatchResult.COMPLETED
    rec = attempts.inserted[0]
    assert rec["account_id"] == 200
    assert rec["source_account_id"] == 100
    assert rec["target_account_id"] == 200


@pytest.mark.asyncio
async def test_e4_begin_execution_called_once_per_execute() -> None:
    """E4: begin_execution_attempt вызывается ровно один раз на реальный execute."""
    queue = _fake_queue()
    attempts = FakeAttempts()
    dispatcher = _dispatcher(
        queue,
        FakeAccounts(),
        FakeTaskTypes(_task_type()),
        attempts=attempts,
    )

    await dispatcher.dispatch(_claimed(26, account_id=99))

    assert queue.begin_calls == [26]
    assert len(attempts.inserted) == 1


@pytest.mark.asyncio
async def test_e4_adapter_error_begin_execution_still_called_once() -> None:
    """E4: при ошибке execute begin_execution_attempt всё равно вызывается один раз."""
    queue = _fake_queue()
    attempts = FakeAttempts()

    class BoomAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise RuntimeError("boom")

    dispatcher = _dispatcher(
        queue,
        FakeAccounts(),
        FakeTaskTypes(_task_type()),
        BoomAdapter(),
        attempts=attempts,
    )

    result = await dispatcher.dispatch(_claimed(27, account_id=99))

    assert result == DispatchResult.RETRIED
    assert queue.begin_calls == [27]
    assert len(attempts.inserted) == 1


@pytest.mark.asyncio
async def test_e4_retry_creates_second_attempt() -> None:
    """E4: два execute → две записи attempt с монотонным attempt_number."""
    queue = _fake_queue()
    attempts = FakeAttempts()

    class FailOnceAdapter(MockTaskAdapter):
        def __init__(self) -> None:
            super().__init__()
            self._failed: set[int] = set()

        async def execute(self, task, *, account):  # type: ignore[override]
            if task.id not in self._failed:
                self._failed.add(task.id)
                raise RuntimeError("transient_fail")
            await super().execute(task, account=account)

    adapter = FailOnceAdapter()
    dispatcher = _dispatcher(
        queue,
        FakeAccounts(),
        FakeTaskTypes(_task_type()),
        adapter,
        attempts=attempts,
    )

    task = _claimed(28, account_id=99)
    result1 = await dispatcher.dispatch(task)
    assert result1 == DispatchResult.RETRIED
    assert len(attempts.inserted) == 1
    assert attempts.inserted[0]["attempt_number"] == 1
    assert attempts.finished[0]["status"] == "error"

    result2 = await dispatcher.dispatch(task)
    assert result2 == DispatchResult.COMPLETED
    assert queue.begin_calls == [28, 28]
    assert len(attempts.inserted) == 2
    assert attempts.inserted[1]["attempt_number"] == 2
    assert attempts.finished[1]["status"] == "success"
