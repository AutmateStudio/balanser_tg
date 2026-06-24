"""C5 — unit- и integration-тесты ResourceChecker + dispatch wiring."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest

from app_balance.queue import db
from app_balance.queue.per_op_reading import TaskType, TaskTypeOp, TaskTypesRepo
from app_balance.queue.resource_check import ResourceChecker
from app_balance.queue.resource_usage import OpAvailability, ResourceUsageRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from app_balance.queue_worker import QueueWorker, WorkerConfig
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_c5_"
_HOLDER_PRIORITY = -2_000_000_000


def _op(
    *,
    op_type_id: int,
    op_code: str,
    account_role: str = "primary",
    op_is_enabled: bool = True,
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
        op_is_enabled=op_is_enabled,
    )


def _task_type_with_ops(
    ops: tuple[TaskTypeOp, ...],
    *,
    min_available_resource_percent: int = 80,
) -> TaskType:
    return TaskType(
        id=10,
        code="parser_add_channel",
        name="parser_add_channel",
        description=None,
        is_enabled=True,
        default_priority=500,
        min_available_resource_percent=min_available_resource_percent,
        requires_specific_account=False,
        uses_two_accounts=False,
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


def _availability(
    account_id: int,
    op_type_id: int,
    op_code: str,
    percent: float,
) -> OpAvailability:
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
    def __init__(self, percents: dict[tuple[int, int], float | None]) -> None:
        self._percents = percents

    async def op_availability(
        self, account_id: int, op_type_id: int
    ) -> OpAvailability | None:
        percent = self._percents.get((account_id, op_type_id))
        if percent is None:
            return None
        return _availability(account_id, op_type_id, f"op_{op_type_id}", percent)


@pytest.mark.asyncio
async def test_all_ops_above_threshold_ok() -> None:
    checker = ResourceChecker(
        FakeUsageRepo({(1, 10): 85.0, (1, 11): 90.0})
    )
    task_type = _task_type_with_ops(
        (_op(op_type_id=10, op_code="get_entity"), _op(op_type_id=11, op_code="join"))
    )

    result = await checker.check_account(1, task_type)

    assert result.ok is True
    assert result.threshold == 80


@pytest.mark.asyncio
async def test_one_op_below_threshold_fails() -> None:
    checker = ResourceChecker(
        FakeUsageRepo({(1, 10): 85.0, (1, 11): 79.0})
    )
    task_type = _task_type_with_ops(
        (_op(op_type_id=10, op_code="get_entity"), _op(op_type_id=11, op_code="join"))
    )

    result = await checker.check_account(1, task_type)

    assert result.ok is False
    assert result.failing_op_code == "join"
    assert result.available_percent == 79.0


@pytest.mark.asyncio
async def test_exact_threshold_passes() -> None:
    checker = ResourceChecker(FakeUsageRepo({(1, 10): 80.0}))
    task_type = _task_type_with_ops((_op(op_type_id=10, op_code="get_entity"),))

    result = await checker.check_account(1, task_type)

    assert result.ok is True


def test_resolve_threshold_no_env_uses_db(monkeypatch: pytest.MonkeyPatch) -> None:
    from app_balance.queue.resource_check import resolve_threshold

    monkeypatch.delenv("RESOURCE_MIN_AVAILABLE_PERCENT", raising=False)
    assert resolve_threshold(80) == 80


def test_resolve_threshold_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from app_balance.queue.resource_check import resolve_threshold

    monkeypatch.setenv("RESOURCE_MIN_AVAILABLE_PERCENT", "50")
    assert resolve_threshold(80) == 50


def test_resolve_threshold_invalid_env_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app_balance.queue.resource_check import resolve_threshold

    monkeypatch.setenv("RESOURCE_MIN_AVAILABLE_PERCENT", "abc")
    assert resolve_threshold(80) == 80


def test_resolve_threshold_out_of_range_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app_balance.queue.resource_check import resolve_threshold

    monkeypatch.setenv("RESOURCE_MIN_AVAILABLE_PERCENT", "150")
    assert resolve_threshold(80) == 80


@pytest.mark.asyncio
async def test_env_override_lets_task_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 66.67% < 80% (БД), но проходит при override=50% (D12 insufficient_resource fix).
    monkeypatch.setenv("RESOURCE_MIN_AVAILABLE_PERCENT", "50")
    checker = ResourceChecker(FakeUsageRepo({(1, 10): 66.67}))
    task_type = _task_type_with_ops((_op(op_type_id=10, op_code="get_entity"),))

    result = await checker.check_account(1, task_type)

    assert result.ok is True
    assert result.threshold == 50


@pytest.mark.asyncio
async def test_skips_disabled_ops() -> None:
    checker = ResourceChecker(FakeUsageRepo({(1, 10): 85.0}))
    task_type = _task_type_with_ops(
        (
            _op(op_type_id=10, op_code="get_entity"),
            _op(op_type_id=11, op_code="disabled_op", op_is_enabled=False),
        )
    )

    result = await checker.check_account(1, task_type)

    assert result.ok is True


@pytest.mark.asyncio
async def test_filters_by_account_role() -> None:
    checker = ResourceChecker(
        FakeUsageRepo({(1, 10): 85.0, (1, 20): 10.0})
    )
    task_type = _task_type_with_ops(
        (
            _op(op_type_id=10, op_code="primary_op", account_role="primary"),
            _op(op_type_id=20, op_code="source_op", account_role="source"),
        )
    )

    primary = await checker.check_account(1, task_type, account_role="primary")
    source = await checker.check_account(1, task_type, account_role="source")

    assert primary.ok is True
    assert source.ok is False
    assert source.failing_op_code == "source_op"


@pytest.mark.asyncio
async def test_empty_ops_fails() -> None:
    checker = ResourceChecker(FakeUsageRepo({}))
    task_type = _task_type_with_ops(())

    result = await checker.check_account(1, task_type)

    assert result.ok is False
    assert result.reason_code == "no_ops_for_role:primary"


@pytest.mark.asyncio
async def test_missing_availability_fails() -> None:
    checker = ResourceChecker(FakeUsageRepo({}))
    task_type = _task_type_with_ops((_op(op_type_id=10, op_code="get_entity"),))

    result = await checker.check_account(1, task_type)

    assert result.ok is False
    assert result.failing_op_code == "get_entity"
    assert result.reason_code == "missing_availability"


@pytest.fixture
async def resource_check_ctx(pg_pool):
    """Аккаунт + op/task_type из seed A9 для integration-тестов C5."""
    session_name = f"{_PREFIX}{uuid.uuid4().hex}"
    dedup_key = f"{_PREFIX}{uuid.uuid4().hex}"

    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_PREFIX}%",
            session_name_like=f"{_PREFIX}%",
        )

    await _cleanup()

    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )
        task_type_id = await conn.fetchval(
            "SELECT id FROM task_types WHERE code = 'parser_add_channel'"
        )
        op_type_id = await conn.fetchval(
            "SELECT id FROM resource_op_types WHERE code = 'get_entity'"
        )

    enqueue = await TaskQueueRepo().enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=dedup_key)
    )

    task_types = TaskTypesRepo()
    task_type = await task_types.get_by_code("parser_add_channel")
    assert task_type is not None

    yield {
        "account_id": account_id,
        "task_id": enqueue.task_id,
        "task_type_id": task_type_id,
        "op_type_id": op_type_id,
        "task_type": task_type,
    }
    await _cleanup()


async def _insert_usage(
    *,
    account_id: int,
    op_type_id: int,
    task_id: int,
    task_type_id: int,
    units: int,
) -> None:
    await ResourceUsageRepo().insert(
        account_id=account_id,
        op_type_id=op_type_id,
        task_id=task_id,
        task_type_id=task_type_id,
        units=units,
    )


async def _insert_account(*, session_suffix: str) -> int:
    session_name = f"{_PREFIX}{session_suffix}_{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO accounts (session_name, status, is_enabled)
            VALUES ($1, 'active', true)
            RETURNING id
            """,
            session_name,
        )


async def _enqueue(
    *,
    priority: int = 2_000_000_000,
    account_id: int | None = None,
) -> int:
    res = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=f"{_PREFIX}{uuid.uuid4().hex}",
            priority=priority,
            account_id=account_id,
        )
    )
    assert res.created and res.task_id is not None
    return res.task_id


async def _occupy_account(account_id: int) -> int:
    holder_task_id = await _enqueue(priority=_HOLDER_PRIORITY)
    async with db.acquire() as conn:
        reserved = await conn.fetchval(
            """
            UPDATE accounts
            SET current_task_id = $2, last_used_at = now()
            WHERE id = $1 AND current_task_id IS NULL
            RETURNING id
            """,
            account_id,
            holder_task_id,
        )
    assert reserved is not None
    return holder_task_id


async def _lock_all_free_accounts_except(exclude_ids: set[int]) -> list[tuple[int, int]]:
    locked: list[tuple[int, int]] = []
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id FROM accounts
            WHERE status IN ('active', 'cooldown')
              AND is_enabled = true
              AND current_task_id IS NULL
              AND (cooldown_until IS NULL OR cooldown_until <= now())
            """
        )
    for row in rows:
        account_id = row["id"]
        if account_id in exclude_ids:
            continue
        holder_task_id = await _occupy_account(account_id)
        locked.append((account_id, holder_task_id))
    return locked


async def _unlock_accounts(locked: list[tuple[int, int]]) -> None:
    for account_id, holder_task_id in locked:
        async with db.acquire() as conn:
            await conn.execute(
                """
                UPDATE accounts
                SET current_task_id = NULL
                WHERE id = $1 AND current_task_id = $2
                """,
                account_id,
                holder_task_id,
            )
            await conn.execute(
                """
                DELETE FROM task_queue
                WHERE id = $1 AND dedup_key LIKE $2
                """,
                holder_task_id,
                f"{_PREFIX}%",
            )


async def _wait_task_status(
    task_id: int, expected: str, *, timeout: float, interval: float = 0.02
) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await _task_status(task_id)
        if status == expected:
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"task {task_id} not {expected} within {timeout}s")


async def _wait_until(predicate, *, timeout: float, interval: float = 0.02) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("condition not met")


async def _task_status(task_id: int) -> str | None:
    async with db.acquire() as conn:
        return await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", task_id
        )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_checker_fresh_account_passes(resource_check_ctx) -> None:
    checker = ResourceChecker(ResourceUsageRepo())
    result = await checker.check_account(
        resource_check_ctx["account_id"],
        resource_check_ctx["task_type"],
    )
    assert result.ok is True


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_checker_depleted_op_fails(resource_check_ctx) -> None:
    await _insert_usage(
        account_id=resource_check_ctx["account_id"],
        op_type_id=resource_check_ctx["op_type_id"],
        task_id=resource_check_ctx["task_id"],
        task_type_id=resource_check_ctx["task_type_id"],
        units=2,
    )

    checker = ResourceChecker(ResourceUsageRepo())
    result = await checker.check_account(
        resource_check_ctx["account_id"],
        resource_check_ctx["task_type"],
    )

    assert result.ok is False
    assert result.failing_op_code == "get_entity"
    assert result.available_percent is not None
    assert result.available_percent < 80


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_postpones_low_resource_no_usage(resource_check_ctx) -> None:
    depleted_id = resource_check_ctx["account_id"]
    await _insert_usage(
        account_id=depleted_id,
        op_type_id=resource_check_ctx["op_type_id"],
        task_id=resource_check_ctx["task_id"],
        task_type_id=resource_check_ctx["task_type_id"],
        units=2,
    )

    task_id = await _enqueue(account_id=depleted_id)

    worker = QueueWorker(
        WorkerConfig(
            worker_id="c5-it-postpone",
            poll_interval_seconds=0.01,
            task_type_codes=["parser_add_channel"],
        )
    )

    run_task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(
            _wait_task_status(task_id, "scheduled", timeout=5.0),
            timeout=5.0,
        )
    finally:
        worker.stop()
        await asyncio.wait_for(run_task, timeout=5.0)

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT status, postpone_count, last_error
            FROM task_queue WHERE id = $1
            """,
            task_id,
        )
        usage_count = await conn.fetchval(
            "SELECT COUNT(*) FROM account_resource_usage WHERE task_id = $1",
            task_id,
        )

    assert row["status"] == "scheduled"
    assert row["postpone_count"] == 1
    assert "insufficient_resource" in (row["last_error"] or "")
    assert usage_count == 0


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_picks_second_account_with_resource(resource_check_ctx) -> None:
    depleted_id = resource_check_ctx["account_id"]
    fresh_id = await _insert_account(session_suffix="fresh")
    await _insert_usage(
        account_id=depleted_id,
        op_type_id=resource_check_ctx["op_type_id"],
        task_id=resource_check_ctx["task_id"],
        task_type_id=resource_check_ctx["task_type_id"],
        units=2,
    )

    locked = await _lock_all_free_accounts_except({depleted_id, fresh_id})
    try:
        task_id = await _enqueue()

        worker = QueueWorker(
            WorkerConfig(
                worker_id="c5-it-second-acc",
                poll_interval_seconds=0.01,
                task_type_codes=["parser_add_channel"],
            )
        )

        run_task = asyncio.create_task(worker.run())
        try:
            await asyncio.wait_for(
                _wait_until(lambda: worker.processed >= 1, timeout=5.0),
                timeout=5.0,
            )
        finally:
            worker.stop()
            await asyncio.wait_for(run_task, timeout=5.0)

        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, account_id FROM task_queue WHERE id = $1",
                task_id,
            )
            fresh_current = await conn.fetchval(
                "SELECT current_task_id FROM accounts WHERE id = $1",
                fresh_id,
            )

        assert row["status"] == "done"
        assert row["account_id"] == fresh_id
        assert fresh_current is None
    finally:
        await _unlock_accounts(locked)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_all_auto_pick_accounts_depleted_postpones(
    resource_check_ctx,
) -> None:
    """C5: все свободные аккаунты без ресурса → postpone, воркер не зависает."""
    depleted_a = resource_check_ctx["account_id"]
    depleted_b = await _insert_account(session_suffix="depleted_b")
    await _insert_usage(
        account_id=depleted_a,
        op_type_id=resource_check_ctx["op_type_id"],
        task_id=resource_check_ctx["task_id"],
        task_type_id=resource_check_ctx["task_type_id"],
        units=2,
    )
    await _insert_usage(
        account_id=depleted_b,
        op_type_id=resource_check_ctx["op_type_id"],
        task_id=resource_check_ctx["task_id"],
        task_type_id=resource_check_ctx["task_type_id"],
        units=2,
    )

    locked = await _lock_all_free_accounts_except({depleted_a, depleted_b})
    try:
        task_id = await _enqueue()

        worker = QueueWorker(
            WorkerConfig(
                worker_id="c5-it-all-depleted",
                poll_interval_seconds=0.01,
                task_type_codes=["parser_add_channel"],
            )
        )

        run_task = asyncio.create_task(worker.run())
        try:
            await asyncio.wait_for(
                _wait_task_status(task_id, "scheduled", timeout=5.0),
                timeout=5.0,
            )
        finally:
            worker.stop()
            await asyncio.wait_for(run_task, timeout=5.0)

        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, postpone_count, last_error FROM task_queue WHERE id = $1",
                task_id,
            )
            usage_count = await conn.fetchval(
                "SELECT COUNT(*) FROM account_resource_usage WHERE task_id = $1",
                task_id,
            )

        assert row["status"] == "scheduled"
        assert row["postpone_count"] == 1
        assert "insufficient_resource" in (row["last_error"] or "")
        assert usage_count == 0
    finally:
        await _unlock_accounts(locked)
