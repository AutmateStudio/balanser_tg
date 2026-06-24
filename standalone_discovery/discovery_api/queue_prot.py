"""D3/D11 — единая точка импорта queue adapter для discovery."""
from app_balance.queue.adapter import ClumpTaskAdapter, execute_task

__all__ = ["ClumpTaskAdapter", "execute_task"]
