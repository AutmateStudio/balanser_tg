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
        # Сброс залипших cooldown до снимка — иначе UI вечно показывает cooldown
        # после истечения таймера (status сбрасывался только в pick_and_reserve).
        cleared = await _repo.clear_expired_cooldowns()
        if cleared:
            log.info(
                "account_queue_overlay: сброшен истёкший cooldown у %d акк.: %s",
                len(cleared),
                ", ".join(cleared[:10]) + ("…" if len(cleared) > 10 else ""),
            )
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
    from discovery_api.account_registry import normalize_session_name

    states = pg_states if pg_states is not None else await fetch_pg_queue_states()
    now_utc = now or datetime.now(timezone.utc)
    # Индекс по basename — PG хранит нормализованные имена.
    states_by_norm: dict[str, AccountQueueState] = {}
    for key, value in states.items():
        states_by_norm[normalize_session_name(key)] = value
        states_by_norm[key] = value

    result: list[dict[str, Any]] = []
    for row in rows:
        name = row.get("session_name") or ""
        pg = states_by_norm.get(name) or states_by_norm.get(normalize_session_name(name))
        result.append(overlay_queue_state(row, pg, now=now_utc))
    return result


async def enrich_channel_counts_from_pg(rows: list[dict[str, Any]]) -> None:
    """Добирает channel_count из PG одним batch (вместо N+1 на /accounts/all).

    Меняет rows in-place. Пропускает строки, у которых уже есть channel_count>0
    и in_clump=True (clump — источник истины для слушаемых каналов).
    Ошибки PG глотаются: дашборд не должен падать из‑за overlay.
    """
    if not get_use_pg_queue() or not rows:
        return
    from discovery_api.account_registry import normalize_session_name

    need: list[dict[str, Any]] = []
    for row in rows:
        if int(row.get("channel_count") or 0) > 0 and row.get("in_clump"):
            continue
        need.append(row)
    if not need:
        return

    try:
        from app_balance.queue import db
        from app_balance.queue.accounts import AccountsRepo
        from app_balance.queue.source_channels import SourceChannelsRepo

        await db.init_pool()
        names = [str(r.get("session_name") or "") for r in need]
        id_by_name = await AccountsRepo().get_ids_by_session_names(names)
        if not id_by_name:
            return
        counts = await SourceChannelsRepo().count_channels_by_accounts(
            list(id_by_name.values())
        )
        for row in need:
            norm = normalize_session_name(str(row.get("session_name") or ""))
            account_id = id_by_name.get(norm)
            if account_id is None:
                continue
            pg_count = int(counts.get(account_id, 0))
            if pg_count > int(row.get("channel_count") or 0):
                row["channel_count"] = pg_count
    except Exception:
        log.debug("enrich_channel_counts_from_pg: skipped", exc_info=True)
