"""E6 — идемпотентный per-op пайплайн (ТЗ §29).

При повторе многошаговой задачи (`collect_extra_data`, `update_channel`)
выполнение продолжается с шага после `payload.last_completed_step`. Порядок
шагов берётся из `ops_catalog.TASK_TYPE_OPS` (каноническая pipeline-
последовательность), детали op (op_type_id, units, role) — из `TaskType.ops`,
загруженных из БД, по совпадению `op_code`.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app_balance.queue.ops_catalog import TASK_TYPE_OPS
from app_balance.queue.per_op_reading import TaskType, TaskTypeOp
from app_balance.queue.task_queue import ClaimedTask

LAST_COMPLETED_STEP_KEY = "last_completed_step"


@dataclass(frozen=True, slots=True)
class PipelineStep:
    """Один шаг пайплайна: позиция, op-код и детали op из task_type_ops."""

    index: int
    op_code: str
    op: TaskTypeOp


@runtime_checkable
class _StepCompletionSink(Protocol):
    async def set_last_completed_step(self, task_id: int, step: str) -> None: ...


@runtime_checkable
class _UsageSink(Protocol):
    async def record_op(
        self,
        *,
        task_type_id: int,
        task_id: int,
        op: TaskTypeOp,
        account_id: int,
        task_attempt_id: int | None = ...,
    ) -> int: ...


OpExecutor = Callable[[PipelineStep], Awaitable[None]]


def get_last_completed_step(payload: Mapping[str, Any] | None) -> str | None:
    """Извлекает `last_completed_step` из payload (или None, если шага нет)."""
    if not payload:
        return None
    value = payload.get(LAST_COMPLETED_STEP_KEY)
    if isinstance(value, str) and value:
        return value
    return None


def ordered_pipeline(task_type: TaskType) -> list[PipelineStep]:
    """Упорядоченный список enabled-op типа задачи (порядок из ops_catalog).

    Op-коды, отсутствующие среди enabled `task_type.ops`, пропускаются — учёт
    ресурса ведётся только по реально сконфигурированным в БД op.
    """
    catalog = TASK_TYPE_OPS.get(task_type.code)
    if not catalog:
        return []
    ops_by_code: dict[str, TaskTypeOp] = {
        op.op_code: op for op in task_type.ops if op.op_is_enabled
    }
    steps: list[PipelineStep] = []
    for definition in catalog:
        op = ops_by_code.get(definition.op_code)
        if op is None:
            continue
        steps.append(
            PipelineStep(index=len(steps), op_code=definition.op_code, op=op)
        )
    return steps


def remaining_steps(
    task_type: TaskType,
    last_completed_step: str | None,
) -> list[PipelineStep]:
    """Шаги, которые ещё нужно выполнить, с учётом last_completed_step.

    Все шаги вплоть до `last_completed_step` включительно пропускаются. Если
    значение пустое или не найдено в пайплайне — выполняем пайплайн целиком
    (безопасный fallback: лучше повторить, чем потерять шаг).
    """
    steps = ordered_pipeline(task_type)
    if not last_completed_step:
        return steps
    for position, step in enumerate(steps):
        if step.op_code == last_completed_step:
            return steps[position + 1 :]
    return steps


async def run_pipeline(
    task: ClaimedTask,
    *,
    task_type: TaskType,
    account_id: int,
    attempt_id: int | None,
    queue: _StepCompletionSink,
    usage: _UsageSink,
    execute_op: OpExecutor,
) -> None:
    """E6 — выполнить оставшиеся op идемпотентно.

    Для каждого незавершённого op: списать ресурс (до RPC, инвариант D5 §7.3),
    выполнить op через execute_op, затем зафиксировать прогресс в
    payload.last_completed_step. Уже завершённые шаги пропускаются и ресурс за
    них повторно не списывается (ТЗ §29).
    """
    last_completed = get_last_completed_step(task.payload)
    for step in remaining_steps(task_type, last_completed):
        await usage.record_op(
            task_type_id=task_type.id,
            task_id=task.id,
            op=step.op,
            account_id=account_id,
            task_attempt_id=attempt_id,
        )
        await execute_op(step)
        await queue.set_last_completed_step(task.id, step.op_code)
