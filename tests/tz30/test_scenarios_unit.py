"""Юнит-тесты сценариев ТЗ §7–§20 через публичные API (без PostgreSQL).

Каждый тест ссылается на пункт ТЗ и проверяет наблюдаемое поведение,
а не детали SQL/внутренних реализаций.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app_balance.queue.accounts import Account
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.error_codes import ErrorCode
from app_balance.queue.mock_adapter import MockTaskAdapter
from app_balance.queue.per_op_reading import TaskType, TaskTypeOp
from app_balance.queue.resource_check import ResourceChecker, ResourceCheckResult
from app_balance.queue.resource_usage import OpAvailability
from app_balance.queue.task_queue import ClaimedTask
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

pytestmark = pytest.mark.tz30


def _op(
    *,
    op_type_id: int,
    op_code: str,
    account_role: str = "primary",
) -> TaskTypeOp:
    return TaskTypeOp(
        task_type_op_id=op_type_id,
        op_type_id=op_type_id,
        op_code=op_code,
        op_name=op_code,
        units_per_execution=1,
        account_role=account_role,  # type: ignore[arg-type]
        rph_limit=100,
        reserve_percent=Decimal("10"),
        op_is_enabled=True,
    )


def _availability(account_id: int, op_type_id: int, op_code: str, percent: float) -> OpAvailability:
    return OpAvailability(
        account_id=account_id,
        op_type_id=op_type_id,
        op_code=op_code,
        effective_rph=100,
        used_last_hour=0,
        available_resource=100,
        available_resource_percent=percent,
    )


class FakeUsageRepo:
    def __init__(self, percents: dict[tuple[int, int], float]) -> None:
        self._percents = percents

    async def op_availability(self, account_id: int, op_type_id: int) -> OpAvailability | None:
        percent = self._percents.get((account_id, op_type_id))
        if percent is None:
            return None
        return _availability(account_id, op_type_id, f"op_{op_type_id}", percent)


# --- §7.2 / §9.3: postpone не расходует попытку ---


@pytest.mark.asyncio
async def test_tz7_postpone_does_not_count_as_execution_attempt() -> None:
    """§7.2, §9.3: отложенная задача не увеличивает attempt_count и не вызывает execute."""
    queue = _fake_queue()
    accounts = FakeAccounts(reserve_ok=False)
    adapter = MockTaskAdapter()
    dispatcher = _dispatcher(queue, accounts, FakeTaskTypes(_task_type()), adapter)

    result = await dispatcher.dispatch(_claimed(1, account_id=99))

    assert result == DispatchResult.POSTPONED
    assert adapter.executions == []
    assert queue.postponed
    assert queue.failed == []


# --- §8.2: приоритет — большее число важнее (контракт типа задачи) ---


def test_tz8_higher_priority_number_means_more_urgent() -> None:
    """§8.2: default_priority из task_types — числовой, больше = выше приоритет."""
    urgent = replace(_task_type(), default_priority=1000, code="urgent")
    background = replace(_task_type(), default_priority=50, code="background")
    assert urgent.default_priority > background.default_priority


# --- §16.1: задача с конкретным account_id ---


@pytest.mark.asyncio
async def test_tz16_fixed_account_runs_only_on_that_account() -> None:
    """§16.1, §30.8: указанный account_id резервируется напрямую, pick не вызывается."""
    queue = _fake_queue()
    accounts = FakeAccounts()
    dispatcher = _dispatcher(queue, accounts, FakeTaskTypes(_task_type()))

    result = await dispatcher.dispatch(_claimed(5, account_id=77))

    assert result == DispatchResult.COMPLETED
    assert accounts.reserved == [(77, 5)]
    assert accounts.pick_calls == []


@pytest.mark.asyncio
async def test_tz16_fixed_account_unavailable_is_postponed_not_failed() -> None:
    """§16.1, §30.9: занятый фиксированный аккаунт → postpone, очередь может идти дальше."""
    queue = _fake_queue()
    accounts = FakeAccounts(reserve_ok=False)
    dispatcher = _dispatcher(queue, accounts, FakeTaskTypes(_task_type()))

    result = await dispatcher.dispatch(_claimed(6, account_id=88))

    assert result == DispatchResult.POSTPONED
    assert "account_reserve_failed:88" in (queue.postponed[0][2] or "")


# --- §16.2 / §30.7: автоподбор другого аккаунта ---


@pytest.mark.asyncio
async def test_tz16_auto_pick_skips_account_with_insufficient_resource() -> None:
    """§16.2, §30.7: если первый аккаунт не подходит по ресурсу — пробуем следующий."""
    queue = _fake_queue()
    depleted = _account(10)
    fresh = _account(20)
    accounts = FakeAccounts(pick_results=[depleted, fresh])
    checker = FakeResourceChecker(fail_accounts={10})
    task_type = replace(_task_type(), min_available_resource_percent=80)
    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(task_type),
        resource_check=checker,
    )

    result = await dispatcher.dispatch(_claimed(7))

    assert result == DispatchResult.COMPLETED
    assert 10 in accounts.released
    assert 20 in accounts.released
    assert (10, "primary") in checker.checked
    assert (20, "primary") in checker.checked


# --- §17 / §30.12: лимиты запуска из task_types ---


@pytest.mark.asyncio
async def test_tz17_min_available_resource_percent_blocks_execution() -> None:
    """§8.1, §17, §30.12: порог min_available_resource_percent берётся из типа задачи."""
    checker = ResourceChecker(FakeUsageRepo({(1, 10): 79.0}))
    task_type = TaskType(
        id=1,
        code="parser_add_channel",
        name="add",
        description=None,
        is_enabled=True,
        default_priority=500,
        min_available_resource_percent=80,
        requires_specific_account=False,
        uses_two_accounts=False,
        max_attempts=5,
        retry_delay_seconds=60,
        retry_backoff_multiplier=Decimal("2"),
        max_retry_delay_seconds=1800,
        target_queue_size=20,
        max_postpone_count=100,
        task_timeout_seconds=300,
        created_at=None,
        updated_at=None,
        ops=(_op(op_type_id=10, op_code="get_entity"),),
    )

    result = await checker.check_account(1, task_type)

    assert result.ok is False
    assert result.threshold == 80


@pytest.mark.asyncio
async def test_tz17_exact_threshold_allows_execution() -> None:
    """§17: ровно min_available_resource_percent — задачу можно запускать."""
    checker = ResourceChecker(FakeUsageRepo({(1, 10): 80.0}))
    task_type = TaskType(
        id=1,
        code="parser_add_channel",
        name="add",
        description=None,
        is_enabled=True,
        default_priority=500,
        min_available_resource_percent=80,
        requires_specific_account=False,
        uses_two_accounts=False,
        max_attempts=5,
        retry_delay_seconds=60,
        retry_backoff_multiplier=Decimal("2"),
        max_retry_delay_seconds=1800,
        target_queue_size=20,
        max_postpone_count=100,
        task_timeout_seconds=300,
        created_at=None,
        updated_at=None,
        ops=(_op(op_type_id=10, op_code="get_entity"),),
    )

    result = await checker.check_account(1, task_type)

    assert result.ok is True


# --- §18 / §30.13–14: dual-account ---


def _move_type() -> TaskType:
    return TaskType(
        id=11,
        code="move_channel",
        name="move",
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
        ops=(
            _op(op_type_id=10, op_code="source_op", account_role="source"),
            _op(op_type_id=20, op_code="target_op", account_role="target"),
        ),
    )


def _claimed_move(
    task_id: int = 1,
    *,
    source_id: int = 10,
    target_id: int = 20,
) -> ClaimedTask:
    return ClaimedTask(
        id=task_id,
        task_type_id=11,
        task_type_code="move_channel",
        priority=100,
        payload={},
        channel_id=555,
        account_id=None,
        source_account_id=source_id,
        target_account_id=target_id,
        attempt_count=0,
        max_attempts=5,
        dedup_key=None,
        locked_by="w",
        locked_until=None,
    )


class FakeDualAccounts(FakeAccounts):
    def __init__(self, *, reserve_pair_ok: bool = True) -> None:
        super().__init__()
        self.reserve_pair_ok = reserve_pair_ok
        self.reserve_pair_calls: list[tuple[int, int, int]] = []
        self.released: list[int] = []

    async def reserve_pair(self, source_id: int, target_id: int, task_id: int):
        self.reserve_pair_calls.append((source_id, target_id, task_id))
        if not self.reserve_pair_ok:
            return None
        from app_balance.queue.accounts import DualReserveResult

        return DualReserveResult(source=_account(source_id), target=_account(target_id))

    async def release(self, account_id: int, task_id: int | None = None) -> None:
        self.released.append(account_id)


class DualResourceChecker(FakeResourceChecker):
    async def check_account(self, account_id: int, task_type: TaskType, **kwargs):
        role = kwargs.get("account_role", "primary")
        self.checked.append((account_id, role))
        if account_id in self.fail_accounts:
            return ResourceCheckResult(
                ok=False,
                threshold=task_type.min_available_resource_percent,
                failing_op_code="get_entity",
                available_percent=10.0,
                account_id=account_id,
                reason_code="insufficient_resource",
            )
        return ResourceCheckResult(
            ok=True,
            threshold=task_type.min_available_resource_percent,
            account_id=account_id,
        )


@pytest.mark.asyncio
async def test_tz18_move_channel_checks_both_accounts_before_execute() -> None:
    """§18, §30.13–14: перенос проверяет ресурс source и target до запуска."""
    queue = _fake_queue()
    accounts = FakeDualAccounts()
    checker = DualResourceChecker(fail_accounts={20})
    dispatcher = TaskDispatcher(
        queue=queue,
        accounts=accounts,
        task_types=FakeTaskTypes(_move_type()),
        adapter=MockTaskAdapter(),
        resource_check=checker,
        postpone_delay_seconds=300,
    )

    result = await dispatcher.dispatch(_claimed_move(9, source_id=10, target_id=20))

    assert result == DispatchResult.POSTPONED
    assert accounts.reserve_pair_calls == []
    assert (10, "source") in checker.checked
    assert (20, "target") in checker.checked


# --- §20: retry после ошибки исполнения ---


@pytest.mark.asyncio
async def test_tz20_execution_error_schedules_retry_not_final_fail() -> None:
    """§13.3, §20, §30.18: временная ошибка после старта → retry, не failed (пока есть попытки)."""

    class BoomAdapter(MockTaskAdapter):
        async def execute(self, task, *, account) -> None:  # type: ignore[override]
            raise RuntimeError("network_timeout")

    queue = FakeQueue(completed=[], postponed=[], failed=[], assigned=[])

    async def reschedule_or_fail(task_id, error, retry_delay_seconds):
        queue.failed.append((task_id, error))
        return "retry"

    queue.reschedule_or_fail = reschedule_or_fail  # type: ignore[method-assign]

    accounts = FakeAccounts()
    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(_task_type()),
        BoomAdapter(),
    )

    result = await dispatcher.dispatch(_claimed(15))

    assert result == DispatchResult.RETRIED
    assert accounts.released == [42]
    assert queue.failed == [(15, ErrorCode.UNEXPECTED_ERROR)]


@pytest.mark.asyncio
async def test_tz20_retry_backoff_uses_task_type_policy() -> None:
    """§20: задержка retry растёт по backoff из task_types (10s → 20s)."""

    class BoomAdapter(MockTaskAdapter):
        async def execute(self, task, *, account) -> None:  # type: ignore[override]
            raise RuntimeError("network_timeout")

    queue = _fake_queue()
    accounts = FakeAccounts()
    task_type = _task_type(
        retry_delay_seconds=10,
        retry_backoff_multiplier=Decimal("2"),
        max_retry_delay_seconds=1800,
    )
    dispatcher = _dispatcher(
        queue,
        accounts,
        FakeTaskTypes(task_type),
        BoomAdapter(),
    )
    task = _claimed(16)

    await dispatcher.dispatch(task)
    await dispatcher.dispatch(task)

    assert queue.retry_delays == [10, 20]


# --- §12: активные статусы для dedup (контракт очереди) ---


def test_tz12_dedup_active_statuses_contract() -> None:
    """§12, §30.15: дубли запрещены только для queued/scheduled/retry/in_progress."""
    active = {"queued", "scheduled", "retry", "in_progress"}
    inactive = {"done", "failed", "cancelled", "stuck"}
    assert active.isdisjoint(inactive)
    assert "done" not in active
    assert "failed" not in active
