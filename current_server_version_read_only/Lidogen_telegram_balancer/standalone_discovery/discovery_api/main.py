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
from discovery_api.config import get_inprocess_worker, get_use_pg_queue
from discovery_api.parser_router import parser_router, restore_persisted_parsers, setup_parser_services
from discovery_api.router import router
from discovery_api.session_registry import release_all, start_health_monitor
from discovery_api.account_registry import sync_accounts_from_disk

log = logging.getLogger(__name__)

# D12 — in-process queue-worker (Вариант A): исполняет задачи в том же процессе,
# что и discovery API, разделяя in-memory clump через get_clump.
_inprocess_worker: Any = None
_inprocess_worker_task: "asyncio.Task[None] | None" = None


async def _start_inprocess_worker() -> None:
    """Поднимает worker-loop в текущем event loop (пул PG уже инициализирован)."""
    global _inprocess_worker, _inprocess_worker_task
    from app_balance.queue_worker import (
        QueueWorker,
        WorkerConfig,
        build_default_dispatcher,
    )

    config = WorkerConfig.from_env()
    _inprocess_worker = QueueWorker(config, dispatcher=build_default_dispatcher(config))
    _inprocess_worker_task = asyncio.create_task(_inprocess_worker.run())
    log.info("D12: in-process queue-worker запущен в процессе discovery")


async def _stop_inprocess_worker() -> None:
    global _inprocess_worker, _inprocess_worker_task
    if _inprocess_worker is not None:
        _inprocess_worker.stop()
    if _inprocess_worker_task is not None:
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(_inprocess_worker_task, timeout=30)
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

