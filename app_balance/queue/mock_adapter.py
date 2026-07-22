"""C2 — mock-адаптер задач (до D3 Telethon)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app_balance.queue.accounts import Account
from app_balance.queue.per_op_reading import TaskType
from app_balance.queue.task_queue import ClaimedTask


@runtime_checkable
class TaskAdapter(Protocol):
    async def execute(
        self,
        task: ClaimedTask,
        *,
        account: Account,
        task_type: TaskType | None = None,
        attempt_id: int | None = None,
    ) -> None:
        """Выполнить задачу.

        Для multi-op типов (F6/F7) dispatch передаёт `task_type` и `attempt_id`,
        чтобы adapter мог запустить idempotent per-op пайплайн с привязкой usage
        к попытке. Single-call адаптеры эти аргументы игнорируют.
        """
        ...


@dataclass(frozen=True, slots=True)
class MockExecution:
    task_id: int
    task_type_code: str
    session_name: str
    payload: dict[str, Any]


class MockTaskAdapter:
    """Записывает факт execute; не вызывает Telethon и не пишет в PG."""

    def __init__(self) -> None:
        self.executions: list[MockExecution] = []

    async def execute(
        self,
        task: ClaimedTask,
        *,
        account: Account,
        task_type: TaskType | None = None,
        attempt_id: int | None = None,
    ) -> None:
        self.executions.append(
            MockExecution(
                task_id=task.id,
                task_type_code=task.task_type_code,
                session_name=account.session_name,
                payload=dict(task.payload),
            )
        )


def default_mock_adapter() -> MockTaskAdapter:
    return MockTaskAdapter()
