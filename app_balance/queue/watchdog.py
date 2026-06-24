"""C6 — watchdog: in_progress → stuck по task_timeout_seconds (ТЗ §13.4)."""
from __future__ import annotations

import asyncio
import logging

from app_balance.queue.task_queue import StuckTaskResult, TaskQueueRepo

logger = logging.getLogger(__name__)


class StuckTaskWatchdog:
    """Фоновый опрос зависших задач и перевод их в stuck."""

    def __init__(
        self,
        queue: TaskQueueRepo,
        *,
        interval_seconds: float,
        stop: asyncio.Event,
        batch_limit: int = 100,
    ) -> None:
        self._queue = queue
        self._interval_seconds = interval_seconds
        self._stop = stop
        self._batch_limit = batch_limit

    async def tick_once(self) -> list[StuckTaskResult]:
        """Один проход: mark stuck + WARNING в лог для каждой задачи."""
        stuck = await self._queue.mark_stuck_timed_out(limit=self._batch_limit)
        for task in stuck:
            logger.warning(
                "watchdog: задача id=%s type=%s locked_by=%s → stuck",
                task.id,
                task.task_type_code,
                task.locked_by,
            )
        return stuck

    async def run(self) -> None:
        """Периодический цикл до сигнала stop."""
        logger.info(
            "watchdog: старт (interval=%.1fs, batch=%d)",
            self._interval_seconds,
            self._batch_limit,
        )
        while not self._stop.is_set():
            try:
                await self.tick_once()
            except Exception:  # noqa: BLE001 — watchdog не должен падать
                logger.exception("watchdog: ошибка tick")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._interval_seconds
                )
            except asyncio.TimeoutError:
                pass
        logger.info("watchdog: остановлен")
