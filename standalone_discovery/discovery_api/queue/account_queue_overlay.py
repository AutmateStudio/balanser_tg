"""Overlay PG queue state на строки аккаунтов для дашборда."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app_balance.queue.account_availability import (
    compute_availability,
    cooldown_remaining_seconds,
)
from app_balance.queue.accounts import AccountQueueState, AccountsRepo
from discovery_api.config import get_use_pg_queue

log = logging.getLogger(__name__)

_repo = AccountsRepo()

# Поля overlay по умолчанию (если PG недоступен или аккаунт не в PG).
_DEFAULT_OVERLAY: dict[str, Any] = {
    "queue_status": None,
    "cooldown_until": None,
    "cooldown_remaining_seconds": None,
    "available_at": None,
    "available_in_seconds": None,
    "flood_until": None,
    "current_task_id": None,
    "last_error_at": None,
    "is_enabled": None,
}


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


async def fetch_pg_queue_states() -> dict[str, AccountQueueState]:
    """Один batch-read PG; пустой dict если USE_PG_QUEUE=false или PG недоступен."""
    if not get_use_pg_queue():
        return {}
    try:
        from app_balance.queue import db

        await db.init_pool()
        return await _repo.list_queue_states()
    except Exception:
        log.warning("account_queue_overlay: не удалось прочитать PG accounts", exc_info=True)
        return {}


def overlay_queue_state(
    row: dict[str, Any],
    pg: AccountQueueState | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Дополняет dict строки аккаунта полями cooldown/available для API."""
    out = dict(row)
    out.update(_DEFAULT_OVERLAY)

    flood_until_unix = row.get("flood_until")
    if flood_until_unix is not None:
        out["flood_until"] = float(flood_until_unix)

    if pg is None:
        now_utc = now or datetime.now(timezone.utc)
        available_at, available_in = compute_availability(
            now=now_utc,
            cooldown_until=None,
            flood_until_unix=flood_until_unix,
        )
        if available_at is not None:
            out["available_at"] = _iso_utc(available_at)
            out["available_in_seconds"] = available_in
        return out

    now_utc = now or datetime.now(timezone.utc)
    cd_rem = cooldown_remaining_seconds(now=now_utc, cooldown_until=pg.cooldown_until)
    cd_until_iso: str | None = None
    if cd_rem is not None and pg.cooldown_until is not None:
        cd_until_iso = _iso_utc(pg.cooldown_until)

    available_at, available_in = compute_availability(
        now=now_utc,
        cooldown_until=pg.cooldown_until if cd_rem is not None else None,
        flood_until_unix=flood_until_unix,
    )

    out["queue_status"] = pg.status
    out["cooldown_until"] = cd_until_iso
    out["cooldown_remaining_seconds"] = cd_rem
    out["available_at"] = _iso_utc(available_at)
    out["available_in_seconds"] = available_in
    out["current_task_id"] = pg.current_task_id
    out["is_enabled"] = pg.is_enabled

    if pg.last_error is not None:
        out["last_error"] = pg.last_error
    out["last_error_at"] = _iso_utc(pg.last_error_at)

    return out


async def overlay_account_rows(
    rows: list[dict[str, Any]],
    *,
    pg_states: dict[str, AccountQueueState] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Overlay для списка аккаунтов; pg_states загружается если не передан."""
    states = pg_states if pg_states is not None else await fetch_pg_queue_states()
    now_utc = now or datetime.now(timezone.utc)
    result: list[dict[str, Any]] = []
    for row in rows:
        name = row.get("session_name") or ""
        pg = states.get(name)
        result.append(overlay_queue_state(row, pg, now=now_utc))
    return result
