"""T1 — unit-тесты resolve_primary_op для task-types RPH API."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app_balance.queue.ops_catalog import (
    COLLECT_EXTRA_DATA,
    MOVE_CHANNEL,
    PARSER_ADD_CHANNEL,
    PARSER_REMOVE_CHANNEL,
    UPDATE_CHANNEL,
)
from app_balance.queue.per_op_reading import TaskType, TaskTypeOp
from app_balance.queue.primary_op import resolve_primary_op


def _op(
    op_code: str,
    *,
    units: int = 1,
    role: str = "primary",
    op_type_id: int = 1,
) -> TaskTypeOp:
    return TaskTypeOp(
        task_type_op_id=1,
        op_type_id=op_type_id,
        op_code=op_code,
        op_name=op_code,
        units_per_execution=units,
        account_role=role,  # type: ignore[arg-type]
        rph_limit=100,
        reserve_percent=Decimal("10"),
        op_is_enabled=True,
    )


def _task_type(code: str, *, uses_two: bool, ops: tuple[TaskTypeOp, ...]) -> TaskType:
    return TaskType(
        id=1,
        code=code,
        name=code,
        description=None,
        is_enabled=True,
        default_priority=100,
        min_available_resource_percent=80,
        requires_specific_account=False,
        uses_two_accounts=uses_two,
        max_attempts=3,
        retry_delay_seconds=60,
        retry_backoff_multiplier=Decimal("2"),
        max_retry_delay_seconds=1800,
        target_queue_size=None,
        max_postpone_count=10,
        task_timeout_seconds=300,
        created_at=None,
        updated_at=None,
        ops=ops,
    )


@pytest.mark.parametrize(
    ("code", "uses_two", "ops", "expected_op"),
    [
        (
            PARSER_ADD_CHANNEL,
            False,
            (
                _op("get_entity", units=2, op_type_id=1),
                _op("channels.JoinChannel", units=2, op_type_id=2),
                _op("channels.GetFullChannel", units=1, op_type_id=3),
            ),
            "channels.JoinChannel",
        ),
        (
            MOVE_CHANNEL,
            True,
            (
                _op("channels.GetParticipant", units=1, role="source", op_type_id=1),
                _op("get_entity", units=2, role="target", op_type_id=2),
                _op("channels.JoinChannel", units=2, role="target", op_type_id=3),
                _op("channels.GetFullChannel", units=1, role="target", op_type_id=4),
            ),
            "channels.JoinChannel",
        ),
        (
            PARSER_REMOVE_CHANNEL,
            False,
            (
                _op("get_entity", units=2, op_type_id=1),
                _op("channels.GetFullChannel", units=1, op_type_id=2),
                _op("channels.LeaveChannel", units=2, op_type_id=3),
            ),
            "channels.LeaveChannel",
        ),
        (
            COLLECT_EXTRA_DATA,
            False,
            (
                _op("get_entity", units=2, op_type_id=1),
                _op("channels.JoinChannel", units=2, op_type_id=2),
                _op("iter_messages", units=1, op_type_id=3),
            ),
            "channels.JoinChannel",
        ),
        (
            UPDATE_CHANNEL,
            False,
            (
                _op("get_entity", units=2, op_type_id=1),
                _op("channels.JoinChannel", units=2, op_type_id=2),
                _op("channels.LeaveChannel", units=2, op_type_id=3),
            ),
            "channels.JoinChannel",
        ),
    ],
)
def test_resolve_primary_op_mvp_codes(
    code: str,
    uses_two: bool,
    ops: tuple[TaskTypeOp, ...],
    expected_op: str,
) -> None:
    task_type = _task_type(code, uses_two=uses_two, ops=ops)
    primary = resolve_primary_op(task_type)
    assert primary.op_code == expected_op


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolve_primary_op_against_seed(pg_pool) -> None:
    """Сверка с реальным seed A9."""
    from app_balance.queue.per_op_reading import TaskTypesRepo

    repo = TaskTypesRepo()
    move = await repo.get_by_code(MOVE_CHANNEL)
    assert move is not None
    assert resolve_primary_op(move).account_role == "target"
    assert resolve_primary_op(move).op_code == "channels.JoinChannel"

    add = await repo.get_by_code(PARSER_ADD_CHANNEL)
    assert add is not None
    assert resolve_primary_op(add).op_code == "channels.JoinChannel"
