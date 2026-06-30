"""Вычисление available_at для дашборда: merge PG cooldown + runtime flood."""
from __future__ import annotations

from datetime import datetime, timezone


def compute_availability(
    *,
    now: datetime,
    cooldown_until: datetime | None,
    flood_until_unix: float | None,
) -> tuple[datetime | None, int | None]:
    """Возвращает (available_at, available_in_seconds) или (None, None)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    candidates: list[datetime] = []

    if cooldown_until is not None:
        cd = cooldown_until
        if cd.tzinfo is None:
            cd = cd.replace(tzinfo=timezone.utc)
        else:
            cd = cd.astimezone(timezone.utc)
        if cd > now:
            candidates.append(cd)

    if flood_until_unix is not None and flood_until_unix > now.timestamp():
        candidates.append(
            datetime.fromtimestamp(flood_until_unix, tz=timezone.utc)
        )

    if not candidates:
        return None, None

    available_at = max(candidates)
    return available_at, max(0, int((available_at - now).total_seconds()))


def cooldown_remaining_seconds(
    *,
    now: datetime,
    cooldown_until: datetime | None,
) -> int | None:
    """Остаток PG cooldown в секундах; None если cooldown не активен."""
    if cooldown_until is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cd = cooldown_until
    if cd.tzinfo is None:
        cd = cd.replace(tzinfo=timezone.utc)
    else:
        cd = cd.astimezone(timezone.utc)
    if cd <= now:
        return None
    return max(0, int((cd - now).total_seconds()))
