"""D6 — опциональная синхронизация runtime health discovery → PG accounts.

No-op без QUEUE_DATABASE_URL. Ошибки PG логируются, не пробрасываются в Telethon-путь.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo

log = logging.getLogger(__name__)

_repo = AccountsRepo()


def pg_health_sync_enabled() -> bool:
    return bool(os.getenv("QUEUE_DATABASE_URL", "").strip())


async def _ensure_pool() -> bool:
    if not pg_health_sync_enabled():
        return False
    try:
        await db.init_pool()
        return True
    except Exception:
        log.warning("account_health_sync: init_pool не удался", exc_info=True)
        return False


async def persist_flood_cooldown(session_name: str, seconds: int) -> None:
    """Записывает FloodWait в accounts.cooldown_until (B7 + D6)."""
    secs = max(0, int(seconds or 0))
    name = (session_name or "").strip()
    if secs <= 0 or not name:
        return
    if not await _ensure_pool():
        return
    until = datetime.now(timezone.utc) + timedelta(seconds=secs)
    try:
        ok = await _repo.set_cooldown(name, until)
        if not ok:
            log.debug(
                "account_health_sync: flood для %s — строка accounts не найдена",
                name,
            )
    except Exception:
        log.warning(
            "account_health_sync: не удалось записать cooldown для %s",
            name,
            exc_info=True,
        )


async def persist_banned(session_name: str, reason: str = "") -> None:
    """Записывает ban в accounts.status (B7 + D6)."""
    name = (session_name or "").strip()
    if not name:
        return
    if not await _ensure_pool():
        return
    try:
        ok = await _repo.set_banned(name, reason=(reason or None))
        if not ok:
            log.debug(
                "account_health_sync: ban для %s — строка accounts не найдена",
                name,
            )
    except Exception:
        log.warning(
            "account_health_sync: не удалось записать ban для %s",
            name,
            exc_info=True,
        )
