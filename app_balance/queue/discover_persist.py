"""Маппинг результатов Telegram discover → source_channels + фильтр discussion."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app_balance.queue.db import acquire


@runtime_checkable
class DiscoverPersistItem(Protocol):
    peer_id: int
    title: str
    username: str | None
    participants_count: int | None
    depth: int
    source: str
    score_total: int
    score_breakdown: dict[str, float]
    score_signals: dict[str, Any]
    score_hard_flags: dict[str, bool]
    meta: dict[str, Any]


_telegram_platform_id: int | None = None


async def get_telegram_platform_id() -> int:
    """platform_id для Telegram (env TELEGRAM_PLATFORM_ID или lookup platforms.code='tg')."""
    global _telegram_platform_id
    env_raw = os.getenv("TELEGRAM_PLATFORM_ID", "").strip()
    if env_raw:
        return int(env_raw)
    if _telegram_platform_id is not None:
        return _telegram_platform_id
    async with acquire() as conn:
        val = await conn.fetchval(
            "SELECT id FROM platforms WHERE lower(code) = 'tg' LIMIT 1"
        )
    if val is None:
        raise ValueError("platform 'tg' не найден в platforms")
    _telegram_platform_id = int(val)
    return _telegram_platform_id


def external_channel_id_from_peer(peer_id: int) -> str:
    """Канонический external_channel_id (полный peer_id как строка, как в n8n)."""
    return str(int(peer_id))


def _linked_chat_id(item: DiscoverPersistItem) -> int | None:
    signals = item.score_signals or {}
    raw = signals.get("linked_chat_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_broadcast_channel_item(item: DiscoverPersistItem) -> bool:
    meta = item.meta or {}
    if meta.get("broadcast") is True:
        return True
    signals = item.score_signals or {}
    if meta.get("megagroup") or meta.get("gigagroup"):
        return False
    return signals.get("broadcast") is True


def is_group_item(item: DiscoverPersistItem) -> bool:
    meta = item.meta or {}
    if meta.get("megagroup") or meta.get("gigagroup"):
        return True
    if meta.get("broadcast") is True:
        return False
    matched_seed = getattr(item, "matched_seed", None)
    if matched_seed is not None:
        return True
    return not is_broadcast_channel_item(item)


def should_persist_discovered(item: DiscoverPersistItem) -> bool:
    """Broadcast — только с discussion; группы/supergroup — всегда."""
    if is_group_item(item):
        return True
    if is_broadcast_channel_item(item):
        return _linked_chat_id(item) is not None
    return True


def entity_kind_for_item(item: DiscoverPersistItem) -> str:
    if is_group_item(item):
        return "group"
    if is_broadcast_channel_item(item):
        return "channel"
    return "unknown"


def build_discovered_metadata(item: DiscoverPersistItem) -> dict[str, Any]:
    signals = item.score_signals or {}
    linked = _linked_chat_id(item)
    is_broadcast = is_broadcast_channel_item(item)
    return {
        "name": item.title,
        "username": item.username,
        "about": signals.get("about"),
        "participants_count": item.participants_count,
        "scam": (item.meta or {}).get("scam"),
        "score": item.score_total,
        "score_breakdown": dict(item.score_breakdown or {}),
        "score_signals": dict(signals),
        "score_hard_flags": dict(item.score_hard_flags or {}),
        "linked_chat_id": linked,
        "has_discussion": bool(linked) if is_broadcast else None,
        "entity_kind": entity_kind_for_item(item),
        "source": item.source,
        "depth": item.depth,
        "discover_meta": dict(item.meta or {}),
    }


def build_upsert_fields(item: DiscoverPersistItem) -> dict[str, Any]:
    signals = item.score_signals or {}
    username = (item.username or "").strip() or None
    external_url = f"https://t.me/{username}" if username else None
    about = signals.get("about")
    description = str(about).strip() if about else None
    return {
        "external_channel_id": external_channel_id_from_peer(item.peer_id),
        "name": (item.title or "").strip() or None,
        "description": description,
        "external_url": external_url,
        "metadata": build_discovered_metadata(item),
    }


@dataclass
class PersistStats:
    inserted: int = 0
    updated: int = 0
    skipped_no_discussion: int = 0
    channel_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped_no_discussion": self.skipped_no_discussion,
            "channel_ids": list(self.channel_ids),
        }
