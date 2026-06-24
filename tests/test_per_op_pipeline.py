"""E6 — unit-тесты идемпотентного per-op пайплайна (ТЗ §29)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app_balance.queue.ops_catalog import COLLECT_EXTRA_DATA, TASK_TYPE_OPS
from app_balance.queue.per_op_pipeline import (
    get_last_completed_step,
    ordered_pipeline,
    remaining_steps,
    run_pipeline,
)
from app_balance.queue.per_op_reading import TaskType, TaskTypeOp
from app_balance.queue.task_queue import ClaimedTask, EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_COLLECT_OP_CODES = [op.op_code for op in TASK_TYPE_OPS[COLLECT_EXTRA_DATA]]


def _op(op_type_id: int, op_code: str, *, op_is_enabled: bool = True) -> TaskTypeOp:
    return TaskTypeOp(
        task_type_op_id=op_type_id,
        op_type_id=op_type_id,
        op_code=op_code,
        op_name=op_code,
        units_per_execution=1,
        account_role="primary",
        rph_limit=100,
        reserve_percent=Decimal("10"),
        op_is_enabled=op_is_enabled,
    )


def _collect_task_type(*, disabled_codes: frozenset[str] = frozenset()) -> TaskType:
    # Порядок ops в БД намеренно отличается от ops_catalog (reversed),
    # чтобы проверить, что порядок шагов берётся из каталога.
    ops = tuple(
        _op(idx + 1, code, op_is_enabled=code not in disabled_codes)
        for idx, code in enumerate(reversed(_COLLECT_OP_CODES))
    )
    return TaskType(
        id=42,
        code=COLLECT_EXTRA_DATA,
        name=COLLECT_EXTRA_DATA,
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
        target_queue_size=None,
        max_postpone_count=100,
        task_timeout_seconds=600,
        created_at=None,
        updated_at=None,
        ops=ops,
    )


def _claimed(payload: dict | None = None) -> ClaimedTask:
    return ClaimedTask(
        id=7,
        task_type_id=42,
        task_type_code=COLLECT_EXTRA_DATA,
        priority=500,
        payload=payload or {},
        channel_id=None,
        account_id=99,
        source_account_id=None,
        target_account_id=None,
        attempt_count=1,
        max_attempts=5,
        dedup_key=None,
        locked_by="w",
        locked_until=None,
    )


class FakeQueue:
    def __init__(self) -> None:
        self.steps: list[tuple[int, str]] = []

    async def set_last_completed_step(self, task_id: int, step: str) -> None:
        self.steps.append((task_id, step))


class FakeUsage:
    def __init__(self) -> None:
        self.records: list[tuple[int, str, int | None]] = []

    async def record_op(
        self,
        *,
        task_type_id: int,
        task_id: int,
        op: TaskTypeOp,
        account_id: int,
        task_attempt_id: int | None = None,
    ) -> int:
        self.records.append((account_id, op.op_code, task_attempt_id))
        return len(self.records)


def test_ordered_pipeline_uses_catalog_order() -> None:
    steps = ordered_pipeline(_collect_task_type())
    assert [s.op_code for s in steps] == _COLLECT_OP_CODES
    assert [s.index for s in steps] == list(range(len(_COLLECT_OP_CODES)))


def test_ordered_pipeline_skips_disabled_ops() -> None:
    disabled = frozenset({_COLLECT_OP_CODES[1]})
    steps = ordered_pipeline(_collect_task_type(disabled_codes=disabled))
    assert _COLLECT_OP_CODES[1] not in [s.op_code for s in steps]
    assert len(steps) == len(_COLLECT_OP_CODES) - 1


def test_ordered_pipeline_unknown_task_type_is_empty() -> None:
    task_type = _collect_task_type()
    object.__setattr__(task_type, "code", "no_such_type")
    assert ordered_pipeline(task_type) == []


def test_remaining_steps_without_last_completed_returns_all() -> None:
    steps = remaining_steps(_collect_task_type(), None)
    assert [s.op_code for s in steps] == _COLLECT_OP_CODES


def test_remaining_steps_skips_through_last_completed() -> None:
    last = _COLLECT_OP_CODES[2]
    steps = remaining_steps(_collect_task_type(), last)
    assert [s.op_code for s in steps] == _COLLECT_OP_CODES[3:]


def test_remaining_steps_unknown_step_runs_full_pipeline() -> None:
    steps = remaining_steps(_collect_task_type(), "not_a_real_op")
    assert [s.op_code for s in steps] == _COLLECT_OP_CODES


def test_move_channel_pipeline_preserves_catalog_order() -> None:
    """E7/E6: порядок шагов move_channel берётся из ops_catalog, не из БД."""
    from app_balance.queue.ops_catalog import MOVE_CHANNEL, TASK_TYPE_OPS

    catalog_ops = TASK_TYPE_OPS[MOVE_CHANNEL]
    move_codes = [op.op_code for op in catalog_ops]
    ops = tuple(
        TaskTypeOp(
            task_type_op_id=idx + 1,
            op_type_id=idx + 1,
            op_code=definition.op_code,
            op_name=definition.op_code,
            units_per_execution=definition.units_per_execution,
            account_role=definition.account_role,
            rph_limit=100,
            reserve_percent=Decimal("10"),
            op_is_enabled=True,
        )
        for idx, definition in enumerate(reversed(catalog_ops))
    )
    task_type = TaskType(
        id=99,
        code=MOVE_CHANNEL,
        name=MOVE_CHANNEL,
        description=None,
        is_enabled=True,
        default_priority=500,
        min_available_resource_percent=80,
        requires_specific_account=False,
        uses_two_accounts=True,
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
    steps = ordered_pipeline(task_type)
    assert [s.op_code for s in steps] == move_codes


def test_get_last_completed_step_variants() -> None:
    assert get_last_completed_step(None) is None
    assert get_last_completed_step({}) is None
    assert get_last_completed_step({"last_completed_step": ""}) is None
    assert get_last_completed_step({"last_completed_step": 5}) is None
    assert get_last_completed_step({"last_completed_step": "get_entity"}) == "get_entity"


@pytest.mark.asyncio
async def test_run_pipeline_executes_all_ops_in_order() -> None:
    queue = FakeQueue()
    usage = FakeUsage()
    executed: list[str] = []

    async def execute_op(step) -> None:
        executed.append(step.op_code)

    await run_pipeline(
        _claimed(),
        task_type=_collect_task_type(),
        account_id=99,
        attempt_id=1,
        queue=queue,
        usage=usage,
        execute_op=execute_op,
    )

    assert executed == _COLLECT_OP_CODES
    assert [code for _, code, _ in usage.records] == _COLLECT_OP_CODES
    assert [step for _, step in queue.steps] == _COLLECT_OP_CODES


@pytest.mark.asyncio
async def test_run_pipeline_resumes_after_last_completed_step() -> None:
    """Retry: уже завершённые op не выполняются и ресурс за них не списывается."""
    queue = FakeQueue()
    usage = FakeUsage()
    executed: list[str] = []

    async def execute_op(step) -> None:
        executed.append(step.op_code)

    resumed_payload = {"last_completed_step": _COLLECT_OP_CODES[2]}
    await run_pipeline(
        _claimed(payload=resumed_payload),
        task_type=_collect_task_type(),
        account_id=99,
        attempt_id=2,
        queue=queue,
        usage=usage,
        execute_op=execute_op,
    )

    expected = _COLLECT_OP_CODES[3:]
    assert executed == expected
    assert [code for _, code, _ in usage.records] == expected
    assert [step for _, step in queue.steps] == expected


@pytest.mark.asyncio
async def test_run_pipeline_records_usage_before_execute() -> None:
    """D5/§7.3: ресурс op списывается до выполнения op (RPC)."""
    queue = FakeQueue()
    usage = FakeUsage()
    order: list[str] = []

    async def execute_op(step) -> None:
        order.append(f"exec:{step.op_code}")

    original_record = usage.record_op

    async def tracking_record(**kwargs):
        order.append(f"usage:{kwargs['op'].op_code}")
        return await original_record(**kwargs)

    usage.record_op = tracking_record  # type: ignore[assignment]

    await run_pipeline(
        _claimed(),
        task_type=_collect_task_type(),
        account_id=99,
        attempt_id=1,
        queue=queue,
        usage=usage,
        execute_op=execute_op,
    )

    first_op = _COLLECT_OP_CODES[0]
    assert order.index(f"usage:{first_op}") < order.index(f"exec:{first_op}")


@pytest.mark.asyncio
async def test_run_pipeline_stops_on_op_error_without_marking_step() -> None:
    """Если op падает — шаг не помечается завершённым, ресурс за него списан (реальная попытка)."""
    queue = FakeQueue()
    usage = FakeUsage()
    executed: list[str] = []

    fail_on = _COLLECT_OP_CODES[1]

    async def execute_op(step) -> None:
        executed.append(step.op_code)
        if step.op_code == fail_on:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await run_pipeline(
            _claimed(),
            task_type=_collect_task_type(),
            account_id=99,
            attempt_id=1,
            queue=queue,
            usage=usage,
            execute_op=execute_op,
        )

    # Дошли до упавшего op включительно; ресурс списан за оба запущенных op.
    assert executed == _COLLECT_OP_CODES[:2]
    assert [code for _, code, _ in usage.records] == _COLLECT_OP_CODES[:2]
    # Завершён только первый шаг (до падения второго).
    assert [step for _, step in queue.steps] == _COLLECT_OP_CODES[:1]


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_set_last_completed_step_persists_in_payload_jsonb(pg_pool) -> None:
    """E6 (PG): set_last_completed_step пишет ключ в JSONB payload, не теряя прочие ключи."""
    dedup = f"test_e6_{uuid.uuid4().hex}"
    repo = TaskQueueRepo()
    # run_after в будущем — фоновый queue-worker не заберёт задачу и не
    # зарезервирует аккаунт (изоляция от FK на accounts.current_task_id).
    res = await repo.enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=dedup,
            priority=2_000_000_000,
            payload={"parser_id": "p1", "ref": "@x"},
            run_after=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    assert res.created and res.task_id is not None
    try:
        await repo.set_last_completed_step(res.task_id, "get_entity")

        snapshot = await repo.get_by_id(res.task_id)
        assert snapshot is not None
        assert snapshot.payload.get("last_completed_step") == "get_entity"
        # Прочие ключи payload сохранены.
        assert snapshot.payload.get("parser_id") == "p1"
        assert snapshot.payload.get("ref") == "@x"
    finally:
        await cleanup_queue_test_data(dedup_key_like=f"{dedup}%")
