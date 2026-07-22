"""F1 — общая логика продюсеров: dedup + target_queue_size (ТЗ §8.3, §12).

Продюсеры не должны создавать дубликаты активных задач (dedup_key + B3 enqueue)
и не должны держать в очереди больше задач, чем target_queue_size типа.

Активные статусы — queued, scheduled, retry, in_progress (ТЗ §8.3).

Известное ограничение: проверка count → insert не атомарна; при параллельных
продюсерах возможен кратковременный overshoot на 1 задачу. Для cron (F8) это
приемлемо.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from app_balance.queue.db import acquire
from app_balance.queue.per_op_reading import TaskType, TaskTypesRepo
from app_balance.queue.task_queue import (
    ACTIVE_STATUSES,
    EnqueueInput,
    EnqueueResult,
    TaskQueueRepo,
    UnknownTaskTypeError,
)

_COUNT_ACTIVE_BY_TYPE_SQL = f"""
SELECT COUNT(*)
FROM task_queue
WHERE task_type_id = $1
  AND status IN {ACTIVE_STATUSES}
"""


@dataclass(frozen=True, slots=True)
class ProduceResult:
    """Результат одной попытки enqueue_if_room."""

    created: bool
    task_id: int | None
    existing_task_id: int | None = None
    # B12: "fatal_history" — dedup_key уже terminal failed с постоянной
    # причиной (см. FATAL_ERROR_CODES) — новая задача не создана.
    skipped_reason: Literal["queue_full", "duplicate", "fatal_history"] | None = None
    fatal_error_code: str | None = None


async def count_active_tasks(task_type_id: int) -> int:
    """Число активных задач типа (для target_queue_size, §8.3)."""
    async with acquire() as conn:
        val = await conn.fetchval(_COUNT_ACTIVE_BY_TYPE_SQL, task_type_id)
    return int(val or 0)


class BaseProducer(ABC):
    """Базовый класс продюсеров F2/F4/F5."""

    def __init__(
        self,
        task_queue: TaskQueueRepo | None = None,
        task_types: TaskTypesRepo | None = None,
    ) -> None:
        self._task_queue = task_queue or TaskQueueRepo()
        self._task_types = task_types or TaskTypesRepo()

    async def remaining_capacity(self, task_type: TaskType) -> int | None:
        """Сколько задач ещё можно поставить.

        None — target_queue_size не задан (лимит не применяется).
        """
        if task_type.target_queue_size is None:
            return None
        active = await count_active_tasks(task_type.id)
        return max(0, task_type.target_queue_size - active)

    async def enqueue_if_room(self, data: EnqueueInput) -> ProduceResult:
        """Ставит задачу, если есть место в очереди и нет активного дубликата."""
        task_type = await self._task_types.get_by_code(data.task_type_code)
        if task_type is None or not task_type.is_enabled:
            raise UnknownTaskTypeError(
                f"Тип задачи '{data.task_type_code}' не найден или выключен"
            )

        if task_type.target_queue_size is not None:
            active = await count_active_tasks(task_type.id)
            if active >= task_type.target_queue_size:
                return ProduceResult(
                    created=False,
                    task_id=None,
                    skipped_reason="queue_full",
                )

        enqueue_result: EnqueueResult = await self._task_queue.enqueue(data)
        return _map_enqueue_result(enqueue_result)

    @abstractmethod
    async def produce(self) -> list[ProduceResult]:
        """Один тик продюсера — реализуется в F2/F4/F5."""


def _map_enqueue_result(result: EnqueueResult) -> ProduceResult:
    if result.created:
        return ProduceResult(
            created=True,
            task_id=result.task_id,
        )
    if result.skipped_reason == "fatal_history":
        return ProduceResult(
            created=False,
            task_id=None,
            existing_task_id=result.existing_task_id,
            skipped_reason="fatal_history",
            fatal_error_code=result.fatal_error_code,
        )
    return ProduceResult(
        created=False,
        task_id=None,
        existing_task_id=result.existing_task_id,
        skipped_reason="duplicate",
    )
