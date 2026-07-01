"""Разрешение primary op для RPH task-types API (§4.2 / §10.1 dashboard TZ)."""
from __future__ import annotations

from app_balance.queue.per_op_reading import TaskType, TaskTypeOp

_OPERATOR_AUDIT_CODES = frozenset({"operator_manual", "operator_reset"})


def resolve_primary_op(task_type: TaskType) -> TaskTypeOp:
    """Op с max units_per_execution среди primary; для dual-account — среди target."""
    role = "target" if task_type.uses_two_accounts else "primary"
    candidates = [op for op in task_type.ops if op.account_role == role]
    if not candidates:
        candidates = [op for op in task_type.ops if op.account_role == "primary"]
    if not candidates:
        raise ValueError(f"task type {task_type.code!r} has no ops for role {role!r}")
    max_units = max(op.units_per_execution for op in candidates)
    top = [op for op in candidates if op.units_per_execution == max_units]
    return min(top, key=lambda op: op.op_code)


def is_g6_audit_error_code(error_code: str) -> bool:
    """True если запись audit от G6, а не от оператора."""
    return error_code not in _OPERATOR_AUDIT_CODES
