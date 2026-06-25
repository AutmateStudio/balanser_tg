"""C6/G5 — watchdog: in_progress → stuck / auto-retry по task_timeout_seconds (ТЗ §13.4)."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from app_balance.queue.task_queue import StuckTaskResult, TaskQueueRepo

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


@dataclass(frozen=True, slots=True)
class WatchdogAutoRetryConfig:
    """G5 — политика опционального восстановления зависших задач (ТЗ §13.4).

    enabled=False (default в prod) — watchdog только маркирует stuck (C6).
    """

    enabled: bool = False
    max_attempts: int = 2
    delay_seconds: int = 60

    @classmethod
    def from_env(cls) -> "WatchdogAutoRetryConfig":
        return cls(
            enabled=_env_flag("WATCHDOG_AUTO_RETRY_ENABLED", False),
            max_attempts=int(os.getenv("WATCHDOG_AUTO_RETRY_MAX_ATTEMPTS", "2")),
            delay_seconds=int(os.getenv("WATCHDOG_AUTO_RETRY_DELAY_SECONDS", "60")),
        )


class StuckTaskWatchdog:
    """Фоновый опрос зависших задач: stuck (C6) либо auto-retry (G5)."""

    def __init__(
        self,
        queue: TaskQueueRepo,
        *,
        interval_seconds: float,
        stop: asyncio.Event,
        batch_limit: int = 100,
        auto_retry: WatchdogAutoRetryConfig | None = None,
    ) -> None:
        self._queue = queue
        self._interval_seconds = interval_seconds
        self._stop = stop
        self._batch_limit = batch_limit
        self._auto_retry = auto_retry or WatchdogAutoRetryConfig()

    async def tick_once(self) -> list[StuckTaskResult]:
        """Один проход: перевод зависших задач + лог по исходу каждой."""
        results = await self._queue.mark_stuck_timed_out(
            limit=self._batch_limit,
            auto_retry=self._auto_retry,
        )
        for task in results:
            if task.outcome == "retry":
                logger.info(
                    "watchdog: auto-retry id=%s type=%s → retry "
                    "(run_after=+%ds, watchdog_retry=%d, attempt=%d/%d)",
                    task.id,
                    task.task_type_code,
                    self._auto_retry.delay_seconds,
                    task.watchdog_retry_count,
                    task.attempt_count,
                    task.max_attempts,
                )
            elif task.outcome == "failed":
                logger.warning(
                    "watchdog: auto-retry исчерпан id=%s type=%s → failed "
                    "(watchdog_retry=%d, attempt=%d/%d)",
                    task.id,
                    task.task_type_code,
                    task.watchdog_retry_count,
                    task.attempt_count,
                    task.max_attempts,
                )
            else:
                logger.warning(
                    "watchdog: задача id=%s type=%s locked_by=%s → stuck",
                    task.id,
                    task.task_type_code,
                    task.locked_by,
                )
        return results

    async def run(self) -> None:
        """Периодический цикл до сигнала stop."""
        logger.info(
            "watchdog: старт (interval=%.1fs, batch=%d, auto_retry=%s)",
            self._interval_seconds,
            self._batch_limit,
            self._auto_retry.enabled,
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
