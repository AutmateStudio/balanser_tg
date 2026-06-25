"""C1/C2/Z4 — процесс воркера очереди: asyncio-loop + graceful shutdown по SIGTERM.

Полный dispatch (C2): claim_next → TaskDispatcher (reserve → adapter → complete).
Алгоритм выбора задачи (B4/C7, делегируется из Z4): среди готовых — max priority,
затем случайная из этого tier (ORDER BY random()); следующий tier — после исчерпания.

Запуск:  python -m app_balance.queue_worker
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.mock_adapter import TaskAdapter, default_mock_adapter
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.resource_check import ResourceChecker
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import ClaimedTask, TaskQueueRepo
from app_balance.queue.watchdog import StuckTaskWatchdog, WatchdogAutoRetryConfig

logger = logging.getLogger("queue_worker")

TaskHandler = Callable[[ClaimedTask], Awaitable[None]]


@dataclass(slots=True)
class WorkerConfig:
    worker_id: str = field(default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}")
    poll_interval_seconds: float = 1.0
    lock_ttl_seconds: int = 300
    retry_delay_seconds: int = 60
    postpone_delay_seconds: int = 300
    task_type_codes: list[str] | None = None
    watchdog_enabled: bool = True
    watchdog_interval_seconds: float = 30.0
    watchdog_auto_retry: WatchdogAutoRetryConfig = field(
        default_factory=WatchdogAutoRetryConfig
    )

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        codes_raw = os.getenv("WORKER_TASK_TYPES", "").strip()
        codes = [c.strip() for c in codes_raw.split(",") if c.strip()] or None
        watchdog_raw = os.getenv("WORKER_WATCHDOG_ENABLED", "true").strip().lower()
        return cls(
            worker_id=os.getenv("WORKER_ID", "").strip()
            or f"{socket.gethostname()}:{os.getpid()}",
            poll_interval_seconds=float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "1.0")),
            lock_ttl_seconds=int(os.getenv("WORKER_LOCK_TTL_SECONDS", "300")),
            retry_delay_seconds=int(os.getenv("WORKER_RETRY_DELAY_SECONDS", "60")),
            postpone_delay_seconds=int(
                os.getenv("WORKER_POSTPONE_DELAY_SECONDS", "300")
            ),
            task_type_codes=codes,
            watchdog_enabled=watchdog_raw not in ("0", "false", "no", "off"),
            watchdog_interval_seconds=float(
                os.getenv("WORKER_WATCHDOG_INTERVAL_SECONDS", "30")
            ),
            watchdog_auto_retry=WatchdogAutoRetryConfig.from_env(),
        )


def _worker_task_adapter_mode() -> str:
    return os.getenv("WORKER_TASK_ADAPTER", "mock").strip().lower()


def build_task_adapter() -> TaskAdapter:
    mode = _worker_task_adapter_mode()
    if mode in ("clump", "telethon", "real"):
        from app_balance.queue.adapter import ClumpTaskAdapter

        return ClumpTaskAdapter()
    return default_mock_adapter()


def build_default_dispatcher(config: WorkerConfig) -> TaskDispatcher:
    usage = ResourceUsageRepo()
    return TaskDispatcher(
        queue=TaskQueueRepo(),
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=build_task_adapter(),
        usage=usage,
        resource_check=ResourceChecker(usage),
        postpone_delay_seconds=config.postpone_delay_seconds,
        retry_delay_seconds=config.retry_delay_seconds,
    )


class QueueWorker:
    """Цикл захвата и исполнения задач с корректной остановкой."""

    def __init__(
        self,
        config: WorkerConfig | None = None,
        queue: TaskQueueRepo | None = None,
        accounts: AccountsRepo | None = None,
        dispatcher: TaskDispatcher | None = None,
        handler: TaskHandler | None = None,
    ) -> None:
        self.config = config or WorkerConfig()
        self._queue = queue or TaskQueueRepo()
        self._accounts = accounts or AccountsRepo()
        self._legacy_handler = handler
        if handler is not None:
            self._dispatcher = None
        else:
            self._dispatcher = dispatcher or build_default_dispatcher(self.config)
        self._stop = asyncio.Event()
        self._processed = 0

    @property
    def processed(self) -> int:
        return self._processed

    def stop(self) -> None:
        """Сигнал остановки. Текущая задача дорабатывается, новые не берутся."""
        if not self._stop.is_set():
            logger.info("worker %s: получен сигнал остановки", self.config.worker_id)
        self._stop.set()

    async def run(self) -> None:
        """Главный цикл. Пул должен быть уже инициализирован (см. serve())."""
        logger.info("worker %s: старт цикла", self.config.worker_id)
        while not self._stop.is_set():
            task = await self._queue.claim_next(
                locked_by=self.config.worker_id,
                lock_ttl_seconds=self.config.lock_ttl_seconds,
                task_type_codes=self.config.task_type_codes,
            )
            if task is None:
                await self._idle_wait()
                continue
            await self._process(task)
        logger.info(
            "worker %s: цикл остановлен (обработано %d)",
            self.config.worker_id,
            self._processed,
        )

    async def _process(self, task: ClaimedTask) -> None:
        if self._legacy_handler is not None:
            await self._process_legacy(task)
            return
        result = await self._dispatcher.dispatch(task)
        if result == DispatchResult.COMPLETED:
            self._processed += 1

    async def _process_legacy(self, task: ClaimedTask) -> None:
        """C1-совместимый путь для unit-тестов с inject handler."""
        try:
            await self._legacy_handler(task)
            if await self._queue.complete(task.id):
                self._processed += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "worker %s: ошибка задачи id=%s", self.config.worker_id, task.id
            )
            status = await self._queue.reschedule_or_fail(
                task.id, str(exc), self.config.retry_delay_seconds
            )
            if status is not None:
                logger.warning("задача id=%s переведена в %s", task.id, status)
            else:
                logger.warning(
                    "задача id=%s уже финализирована (watchdog?)",
                    task.id,
                )
        finally:
            if task.account_id is not None:
                await self._accounts.release(task.account_id)

    async def _idle_wait(self) -> None:
        """Пауза между опросами; прерывается сигналом остановки."""
        try:
            await asyncio.wait_for(
                self._stop.wait(), timeout=self.config.poll_interval_seconds
            )
        except asyncio.TimeoutError:
            pass

    async def serve(self) -> None:
        """Полный жизненный цикл: пул + сигналы + watchdog + run + закрытие пула."""
        await db.init_pool()
        if _worker_task_adapter_mode() in ("clump", "telethon", "real"):
            from discovery_api.clump_bootstrap import restore_all_clumps_from_store

            restored = await restore_all_clumps_from_store()
            logger.info("worker %s: restored %d clump(s)", self.config.worker_id, restored)
        _install_signal_handlers(self.stop)
        wd_task: asyncio.Task | None = None
        if self.config.watchdog_enabled:
            watchdog = StuckTaskWatchdog(
                self._queue,
                interval_seconds=self.config.watchdog_interval_seconds,
                stop=self._stop,
                auto_retry=self.config.watchdog_auto_retry,
            )
            wd_task = asyncio.create_task(watchdog.run())
        try:
            await self.run()
        finally:
            if wd_task is not None:
                if not wd_task.done():
                    wd_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await wd_task
            await db.close_pool()


def _install_signal_handlers(stop: Callable[[], None]) -> None:
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop)
        except (NotImplementedError, RuntimeError):
            try:
                signal.signal(sig, lambda *_: stop())
            except (ValueError, OSError):
                pass


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = WorkerConfig.from_env()
    worker = QueueWorker(config, dispatcher=build_default_dispatcher(config))
    asyncio.run(worker.serve())


if __name__ == "__main__":
    main()
