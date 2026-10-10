"""Пульс in-process воркера для /health (тот же процесс, что discovery-api)."""
from __future__ import annotations

import os
import time

_started_at: float | None = None
_last_tick_at: float | None = None


def mark_worker_started() -> None:
    global _started_at, _last_tick_at
    now = time.monotonic()
    if _started_at is None:
        _started_at = now
    _last_tick_at = now


def mark_worker_tick() -> None:
    global _last_tick_at
    _last_tick_at = time.monotonic()


def worker_heartbeat_problem() -> str | None:
    """None — воркер в этом процессе не запускался или тикает вовремя."""
    if _started_at is None:
        return None
    grace = float(os.getenv("HEALTH_WORKER_GRACE_SECONDS", "120"))
    stale_after = float(os.getenv("HEALTH_WORKER_STALE_SECONDS", "180"))
    if time.monotonic() - _started_at < grace:
        return None
    if _last_tick_at is None or time.monotonic() - _last_tick_at > stale_after:
        return "worker"
    return None
