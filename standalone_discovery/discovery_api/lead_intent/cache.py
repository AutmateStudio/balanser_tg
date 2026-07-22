"""Кэш scored_at в source_channels.metadata.lead_intent."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app_balance.queue.db import acquire


def get_cache_days() -> int:
    raw = os.getenv("LEAD_INTENT_CACHE_DAYS", "7").strip()
    try:
        return max(0, min(int(raw), 90))
    except ValueError:
        return 7


def parse_scored_at(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def is_cache_fresh(lead_intent: Optional[dict], *, cache_days: Optional[int] = None) -> bool:
    if not isinstance(lead_intent, dict):
        return False
    days = get_cache_days() if cache_days is None else cache_days
    if days <= 0:
        return False
    scored = parse_scored_at(lead_intent.get("scored_at"))
    if scored is None:
        return False
    return scored >= datetime.now(timezone.utc) - timedelta(days=days)


async def fetch_lead_intent_cache(
    *,
    platform_id: int,
    external_channel_id: str,
) -> Optional[dict]:
    """Читает metadata->lead_intent из source_channels (или None)."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT metadata->'lead_intent' AS lead_intent
            FROM source_channels
            WHERE platform_id = $1 AND external_channel_id = $2
            LIMIT 1
            """,
            platform_id,
            str(external_channel_id),
        )
    if row is None:
        return None
    raw = row["lead_intent"]
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json

        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None
