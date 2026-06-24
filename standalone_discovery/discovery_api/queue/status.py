"""D10 — read-only статус задачи из PostgreSQL task_queue."""
from __future__ import annotations

from app_balance.queue.task_queue import TaskQueueRepo, TaskSnapshot


async def get_task_snapshot(task_id: int) -> TaskSnapshot | None:
    return await TaskQueueRepo().get_by_id(task_id)
