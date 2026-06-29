import asyncio
import contextlib
import json
import logging
import os
import time
from typing import Any

from fastapi import Depends, FastAPI
from starlette.requests import Request

from discovery_api.auth import restore_active_sessions
from discovery_api.api_key_auth import require_api_key
from discovery_api.bot_registry import start_bot_polling_once, stop_bot_polling
from discovery_api.config import get_inprocess_worker, get_inprocess_worker_count, get_use_pg_queue
from discovery_api.parser_router import parser_router, restore_persisted_parsers, setup_parser_services
from discovery_api.router import router
from discovery_api.session_registry import release_all, start_health_monitor
from discovery_api.account_registry import sync_accounts_from_disk

log = logging.getLogger(__name__)

# D12 — in-process worker pool: N параллельных asyncio-задач, общий clump.
# Каждый worker независимо claim'ит задачи; PG-уровень (FOR UPDATE SKIP LOCKED
# + pick_and_reserve) гарантирует, что разные воркеры берут разные задачи и
# разные аккаунты. N задаётся INPROCESS_WORKER_COUNT (дефолт 4).
_inprocess_worker: Any = None  # worker[0] для обратной совместимости с тестами
_inprocess_worker_task: "asyncio.Task[None] | None" = None  # совместимость тестов
_inprocess_worker_pool: "list[Any]" = []
_inprocess_worker_tasks: "list[asyncio.Task[None]]" = []
_inprocess_pool_stop: "asyncio.Event | None" = None  # сигнал остановки watchdog


def _worker_task_done_callback(task: "asyncio.Task[None]") -> None:
    """Логирует необработанные исключения asyncio task'ов пула воркеров.

    asyncio не выводит исключения автоматически до GC task'а. Callback
    гарантирует мгновенное появление ошибки в логах.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(
            "D12: worker task '%s' завершился с исключением: %s",
            task.get_name(),
            exc,
            exc_info=exc,
        )


async def _resilient_worker_loop(
    worker: Any,
    stop: "asyncio.Event",
    *,
    restart_delay: float = 5.0,
) -> None:
    """Обёртка над worker.run() с автоматическим рестартом при необработанных исключениях.

    Если worker.run() упал (любая ошибка кроме CancelledError) — логируем,
    ждём restart_delay секунд и запускаем снова. Это гарантирует, что падение
    из-за transient DB-ошибки (asyncpg) или пробрасывания исключения из
    dispatch.finally не убивает воркер навсегда.
    """
    worker_id = getattr(getattr(worker, "config", None), "worker_id", "?")
    while not stop.is_set():
        try:
            await worker.run()
            # run() вернулся нормально — воркер остановлен штатно
            break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "D12: воркер %s упал с необработанным исключением, перезапуск через %.0fs",
                worker_id,
                restart_delay,
            )
            # Сбрасываем флаг остановки воркера, чтобы следующий run() работал
            try:
                worker._stop.clear()
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=restart_delay)
            except asyncio.TimeoutError:
                pass


async def _start_inprocess_worker() -> None:
    """Поднимает пул из N resilient worker'ов в текущем event loop (пул PG уже инициализирован)."""
    global _inprocess_worker, _inprocess_worker_task
    global _inprocess_worker_pool, _inprocess_worker_tasks, _inprocess_pool_stop
    from app_balance.queue_worker import (
        QueueWorker,
        WorkerConfig,
        build_default_dispatcher,
    )

    n = get_inprocess_worker_count()
    base_config = WorkerConfig.from_env()

    pool_stop = asyncio.Event()
    _inprocess_pool_stop = pool_stop
    _inprocess_worker_pool = []
    _inprocess_worker_tasks = []

    for i in range(n):
        # Уникальный worker_id чтобы различать в логах и PG locked_by
        cfg = WorkerConfig(
            worker_id=f"{base_config.worker_id}-w{i + 1}",
            poll_interval_seconds=base_config.poll_interval_seconds,
            lock_ttl_seconds=base_config.lock_ttl_seconds,
            retry_delay_seconds=base_config.retry_delay_seconds,
            postpone_delay_seconds=base_config.postpone_delay_seconds,
            task_type_codes=base_config.task_type_codes,
            watchdog_enabled=False,  # один watchdog на весь пул (ниже)
            watchdog_interval_seconds=base_config.watchdog_interval_seconds,
            watchdog_auto_retry=base_config.watchdog_auto_retry,
        )
        worker = QueueWorker(cfg, dispatcher=build_default_dispatcher(cfg))
        task = asyncio.create_task(
            _resilient_worker_loop(worker, pool_stop),
            name=f"inprocess-worker-{i + 1}",
        )
        task.add_done_callback(_worker_task_done_callback)
        _inprocess_worker_pool.append(worker)
        _inprocess_worker_tasks.append(task)

    # Один watchdog на весь пул — мониторит stuck-задачи
    if base_config.watchdog_enabled and n > 0:
        from app_balance.queue.watchdog import StuckTaskWatchdog
        from app_balance.queue.task_queue import TaskQueueRepo

        wd = StuckTaskWatchdog(
            TaskQueueRepo(),
            interval_seconds=base_config.watchdog_interval_seconds,
            stop=pool_stop,
            auto_retry=base_config.watchdog_auto_retry,
        )
        wd_task = asyncio.create_task(wd.run(), name="inprocess-watchdog")
        _inprocess_worker_tasks.append(wd_task)

    # Обратная совместимость с тестами, которые обращаются к _inprocess_worker/_task
    _inprocess_worker = _inprocess_worker_pool[0] if _inprocess_worker_pool else None
    _inprocess_worker_task = _inprocess_worker_tasks[0] if _inprocess_worker_tasks else None

    log.info(
        "D12: in-process worker pool запущен (%d воркер(ов)) в процессе discovery", n
    )


async def _stop_inprocess_worker() -> None:
    global _inprocess_worker, _inprocess_worker_task
    global _inprocess_worker_pool, _inprocess_worker_tasks, _inprocess_pool_stop

    # Остановить watchdog через его stop-событие
    if _inprocess_pool_stop is not None:
        _inprocess_pool_stop.set()

    # Сигнализировать каждому воркеру завершить текущую задачу
    for worker in _inprocess_worker_pool:
        with contextlib.suppress(Exception):
            worker.stop()

    if _inprocess_worker_tasks:
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(
                asyncio.gather(*_inprocess_worker_tasks, return_exceptions=True),
                timeout=30,
            )

    _inprocess_worker_pool = []
    _inprocess_worker_tasks = []
    _inprocess_pool_stop = None
    _inprocess_worker = None
    _inprocess_worker_task = None


app = FastAPI(
    title="API Telegram discovery и скоринга",
    description="API для поиска и скоринга кандидатов Telegram каналов/групп",
    version="1.0.0",
)


def _debug_ndjson_append(payload: dict) -> None:
    # #region agent log
    path = os.environ.get(
        "LIDOGEN_DEBUG_NDJSON",
        os.path.join(os.getenv("TEMP") or "/tmp", "debug-ae06a4.log"),
    )
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion


@app.middleware("http")
async def _debug_request_ingest_middleware(request: Request, call_next):
    # #region agent log
    if os.getenv("LIDOGEN_DEBUG_AE06A4") == "1":
        cli = request.client
        _debug_ndjson_append(
            {
                "sessionId": "ae06a4",
                "runId": os.environ.get("LIDOGEN_DEBUG_RUN", "pre-fix"),
                "hypothesisId": "H_APP_LAYER",
                "location": "discovery_api/main.py:_debug_request_ingest_middleware",
                "message": "http_request_received",
                "data": {
                    "client_host": cli.host if cli else None,
                    "client_port": cli.port if cli else None,
                    "method": request.method,
                    "path": request.url.path,
                },
                "timestamp": int(time.time() * 1000),
            }
        )
    # #endregion
    return await call_next(request)


app.include_router(router, dependencies=[Depends(require_api_key)])
app.include_router(parser_router, dependencies=[Depends(require_api_key)])


@app.on_event("startup")
async def on_startup() -> None:
    if get_use_pg_queue():
        from app_balance.queue import db

        await db.init_pool()
        log.info("D8: пул PG task_queue инициализирован (USE_PG_QUEUE=true)")
    start_bot_polling_once()
    sync_accounts_from_disk()
    await restore_active_sessions()
    await restore_persisted_parsers()
    setup_parser_services()
    start_health_monitor()
    if get_use_pg_queue() and get_inprocess_worker():
        await _start_inprocess_worker()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    from discovery_api.action_queue import stop_action_worker

    stop_bot_polling()
    await stop_action_worker()
    await _stop_inprocess_worker()
    await release_all()
    if get_use_pg_queue():
        from app_balance.queue import db

        await db.close_pool()


@app.get("/health", tags=["system"])
async def health():
    return {"status": "в порядке"}

