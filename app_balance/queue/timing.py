"""Лёгкая инструментация тайминга задач воркера (диагностика throughput).

Замеряет для одной задачи:
- ``total_seconds``      — полное время обработки в ``_process``;
- ``telegram_seconds``   — суммарное время Telegram-RPC (add_channel_on_session);
- ``db_held_seconds``    — суммарное время удержания DB-соединения (proxy стоимости
                            SQL; при PG через SSH-туннель это ключевая метрика);
- ``pool_wait_seconds``  — суммарное ожидание свободного соединения в пуле
                            (растёт, когда воркеров больше, чем ``max_size`` пула);
плюс глобальный gauge эффективной конкуренции (сколько задач исполняется
одновременно в процессе) и периодический агрегат средних.

Всё передаётся через ``contextvars``, чтобы не менять сигнатуры функций.
Любая ошибка тайминга НИКОГДА не должна ломать обработку задачи — все точки
входа делают no-op, если инструментация выключена или контекст не установлен.

Управление через env:
- ``WORKER_TIMING_ENABLED`` (default ``1``) — включает замеры;
- ``WORKER_TIMING_LOG`` (default ``1``)     — пер-задачная строка ``TIMING ...``;
- ``WORKER_TIMING_SUMMARY_EVERY`` (default ``100``) — период агрегатного лога.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger("queue_worker.timing")


def _enabled() -> bool:
    return os.getenv("WORKER_TIMING_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _per_task_log_enabled() -> bool:
    return os.getenv("WORKER_TIMING_LOG", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _summary_every() -> int:
    try:
        return max(0, int(os.getenv("WORKER_TIMING_SUMMARY_EVERY", "100")))
    except ValueError:
        return 100


@dataclass
class TaskTiming:
    """Накопитель метрик для одной задачи."""

    total_seconds: float = 0.0
    telegram_seconds: float = 0.0
    telegram_calls: int = 0
    db_held_seconds: float = 0.0
    db_calls: int = 0
    pool_wait_seconds: float = 0.0
    concurrency_at_start: int = 0

    def add_telegram(self, seconds: float) -> None:
        self.telegram_seconds += seconds
        self.telegram_calls += 1

    def add_db(self, held: float, pool_wait: float) -> None:
        self.db_held_seconds += held
        self.pool_wait_seconds += pool_wait
        self.db_calls += 1


_current: contextvars.ContextVar[TaskTiming | None] = contextvars.ContextVar(
    "queue_task_timing", default=None
)

# --- Глобальные счётчики процесса (единый event loop => инкремент безопасен) ---
_active: int = 0
_max_active: int = 0

# --- Агрегаты для периодического среднего ---
_agg_count: int = 0
_agg_total: float = 0.0
_agg_tg: float = 0.0
_agg_db: float = 0.0
_agg_pool: float = 0.0


def current() -> TaskTiming | None:
    return _current.get()


def active_now() -> int:
    return _active


def max_active() -> int:
    return _max_active


@contextlib.contextmanager
def track_task():
    """Оборачивает обработку одной задачи: свежий TaskTiming + gauge конкуренции.

    Возвращает ``TaskTiming`` (или ``None``, если инструментация выключена),
    у которого после выхода из блока заполнено ``total_seconds``.
    """
    global _active, _max_active
    if not _enabled():
        yield None
        return
    timing = TaskTiming()
    token = _current.set(timing)
    _active += 1
    if _active > _max_active:
        _max_active = _active
    timing.concurrency_at_start = _active
    start = time.monotonic()
    try:
        yield timing
    finally:
        timing.total_seconds = time.monotonic() - start
        _active -= 1
        _current.reset(token)


@contextlib.contextmanager
def track_telegram():
    """Замер одного Telegram-RPC-блока (no-op вне контекста задачи)."""
    timing = _current.get()
    if timing is None:
        yield
        return
    start = time.monotonic()
    try:
        yield
    finally:
        timing.add_telegram(time.monotonic() - start)


def record_db(held: float, pool_wait: float) -> None:
    """Учесть удержание DB-соединения и ожидание пула (no-op вне контекста)."""
    timing = _current.get()
    if timing is not None:
        timing.add_db(held, pool_wait)


def note_finished(timing: TaskTiming, *, outcome: str, task_id: int, task_type: str) -> None:
    """Пер-задачный лог + периодический агрегат. Никогда не бросает наружу."""
    global _agg_count, _agg_total, _agg_tg, _agg_db, _agg_pool
    try:
        if _per_task_log_enabled():
            other = timing.total_seconds - timing.telegram_seconds - timing.db_held_seconds
            logger.info(
                "TIMING task_id=%s type=%s outcome=%s total=%.2fs tg=%.2fs(%d) "
                "db=%.2fs(%d) pool_wait=%.2fs other=%.2fs conc=%d max_conc=%d",
                task_id,
                task_type,
                outcome,
                timing.total_seconds,
                timing.telegram_seconds,
                timing.telegram_calls,
                timing.db_held_seconds,
                timing.db_calls,
                timing.pool_wait_seconds,
                max(0.0, other),
                timing.concurrency_at_start,
                _max_active,
            )

        _agg_count += 1
        _agg_total += timing.total_seconds
        _agg_tg += timing.telegram_seconds
        _agg_db += timing.db_held_seconds
        _agg_pool += timing.pool_wait_seconds

        every = _summary_every()
        if every and _agg_count % every == 0:
            n = _agg_count
            logger.info(
                "TIMING-SUMMARY n=%d avg_total=%.2fs avg_tg=%.2fs avg_db=%.2fs "
                "avg_pool_wait=%.2fs max_conc=%d",
                n,
                _agg_total / n,
                _agg_tg / n,
                _agg_db / n,
                _agg_pool / n,
                _max_active,
            )
    except Exception:  # noqa: BLE001 — тайминг не должен ломать обработку
        logger.debug("note_finished: ошибка тайминга проигнорирована", exc_info=True)


def snapshot() -> dict[str, float | int]:
    """Текущий агрегат (для тестов / внешнего опроса)."""
    n = _agg_count or 1
    return {
        "count": _agg_count,
        "avg_total": _agg_total / n,
        "avg_telegram": _agg_tg / n,
        "avg_db_held": _agg_db / n,
        "avg_pool_wait": _agg_pool / n,
        "max_active": _max_active,
        "active_now": _active,
    }
