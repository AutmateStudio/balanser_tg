"""Heartbeat фоновых watchdog / queue-monitor (in-memory + PG monitor_heartbeats)."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app_balance.queue import db

logger = logging.getLogger(__name__)

WATCHDOG_STUCK = "stuck_task_watchdog"
WATCHDOG_SESSION_HEALTH = "session_health_monitor"
WATCHDOG_ACCOUNT_AUTH = "account_auth_watchdog"
WATCHDOG_QUEUE_MONITOR = "queue_monitor"

KNOWN_WATCHDOGS: tuple[str, ...] = (
    WATCHDOG_STUCK,
    WATCHDOG_SESSION_HEALTH,
    WATCHDOG_ACCOUNT_AUTH,
    WATCHDOG_QUEUE_MONITOR,
)

_UPSERT_SQL = """
INSERT INTO monitor_heartbeats (
  name, last_tick_at, last_duration_ms, last_result, last_error,
  interval_seconds, enabled, process, updated_at
) VALUES (
  $1, $2, $3, $4::jsonb, $5, $6, $7, $8, now()
)
ON CONFLICT (name) DO UPDATE SET
  last_tick_at = EXCLUDED.last_tick_at,
  last_duration_ms = EXCLUDED.last_duration_ms,
  last_result = EXCLUDED.last_result,
  last_error = EXCLUDED.last_error,
  interval_seconds = EXCLUDED.interval_seconds,
  enabled = EXCLUDED.enabled,
  process = EXCLUDED.process,
  updated_at = now()
"""

_SELECT_ALL_SQL = """
SELECT name, last_tick_at, last_duration_ms, last_result, last_error,
       interval_seconds, enabled, process
FROM monitor_heartbeats
"""


@dataclass
class WatchdogHeartbeat:
    name: str
    last_tick_at: datetime | None = None
    last_duration_ms: int | None = None
    last_result: dict[str, Any] = field(default_factory=dict)
    last_error: str | None = None
    interval_seconds: float | None = None
    enabled: bool = True
    process: str | None = None

    def is_stale(self, *, now: datetime | None = None) -> bool:
        if self.last_tick_at is None:
            return True
        if not self.enabled:
            return False
        interval = self.interval_seconds if self.interval_seconds and self.interval_seconds > 0 else 60.0
        ref = now or datetime.now(timezone.utc)
        tick = self.last_tick_at
        if tick.tzinfo is None:
            tick = tick.replace(tzinfo=timezone.utc)
        age = (ref - tick).total_seconds()
        return age > 2 * interval

    def to_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        return {
            "name": self.name,
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "last_duration_ms": self.last_duration_ms,
            "last_result": dict(self.last_result),
            "last_error": self.last_error,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
            "process": self.process,
            "stale": self.is_stale(now=now),
        }


class WatchdogRegistry:
    """In-memory registry + best-effort upsert в PG."""

    def __init__(self) -> None:
        self._items: dict[str, WatchdogHeartbeat] = {
            name: WatchdogHeartbeat(name=name, enabled=False)
            for name in KNOWN_WATCHDOGS
        }

    def configure(
        self,
        name: str,
        *,
        interval_seconds: float | None = None,
        enabled: bool = True,
        process: str | None = None,
    ) -> None:
        item = self._items.setdefault(name, WatchdogHeartbeat(name=name))
        item.enabled = enabled
        if interval_seconds is not None:
            item.interval_seconds = interval_seconds
        if process is not None:
            item.process = process

    async def record_tick(
        self,
        name: str,
        *,
        duration_ms: int | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        interval_seconds: float | None = None,
        enabled: bool = True,
        process: str | None = None,
        persist: bool = True,
    ) -> WatchdogHeartbeat:
        now = datetime.now(timezone.utc)
        item = self._items.setdefault(name, WatchdogHeartbeat(name=name))
        item.last_tick_at = now
        item.last_duration_ms = duration_ms
        item.last_result = dict(result or {})
        item.last_error = error
        item.enabled = enabled
        if interval_seconds is not None:
            item.interval_seconds = interval_seconds
        if process is not None:
            item.process = process
        if persist:
            await self._persist(item)
        return item

    async def _persist(self, item: WatchdogHeartbeat) -> None:
        try:
            async with db.acquire() as conn:
                await conn.execute(
                    _UPSERT_SQL,
                    item.name,
                    item.last_tick_at,
                    item.last_duration_ms,
                    json.dumps(item.last_result),
                    item.last_error,
                    item.interval_seconds,
                    item.enabled,
                    item.process,
                )
        except Exception:  # noqa: BLE001 — heartbeat не должен ронять worker
            logger.debug(
                "monitor_heartbeats upsert failed for %s (таблица может отсутствовать)",
                item.name,
                exc_info=True,
            )

    async def load_from_db(self) -> None:
        try:
            async with db.acquire() as conn:
                rows = await conn.fetch(_SELECT_ALL_SQL)
        except Exception:  # noqa: BLE001
            logger.debug("monitor_heartbeats select failed", exc_info=True)
            return
        for row in rows:
            name = str(row["name"])
            raw_result = row["last_result"]
            if isinstance(raw_result, str):
                try:
                    raw_result = json.loads(raw_result)
                except json.JSONDecodeError:
                    raw_result = {}
            elif raw_result is None:
                raw_result = {}
            self._items[name] = WatchdogHeartbeat(
                name=name,
                last_tick_at=row["last_tick_at"],
                last_duration_ms=(
                    int(row["last_duration_ms"])
                    if row["last_duration_ms"] is not None
                    else None
                ),
                last_result=dict(raw_result),
                last_error=row["last_error"],
                interval_seconds=(
                    float(row["interval_seconds"])
                    if row["interval_seconds"] is not None
                    else None
                ),
                enabled=bool(row["enabled"]),
                process=row["process"],
            )

    def list_status(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        # Гарантируем известные имена в ответе.
        for name in KNOWN_WATCHDOGS:
            self._items.setdefault(name, WatchdogHeartbeat(name=name, enabled=False))
        return [
            self._items[name].to_dict(now=now)
            for name in sorted(self._items.keys())
        ]


_registry = WatchdogRegistry()


def get_watchdog_registry() -> WatchdogRegistry:
    return _registry


class TickTimer:
    """Контекст измерения длительности тика."""

    def __init__(self) -> None:
        self._started = time.perf_counter()

    def duration_ms(self) -> int:
        return int((time.perf_counter() - self._started) * 1000)
