from __future__ import annotations

from typing import Any, Dict, Optional, Union

from telethon import TelegramClient
from telethon.tl import functions, types

from discovery_api.chat_resolve import normalize_chat_ref, resolve_listen_target
from discovery_api.discovery import extract_channel_meta
from discovery_api.score_channel.lidgen_scorer import score_channel_for_lidgen
from discovery_api.score_channel.signal_collector import collect_lidgen_signals


def _extract_channel_name_from_link(link: str) -> Union[str, int]:
    """Обёртка над `normalize_chat_ref` для обратной совместимости."""
    return normalize_chat_ref(link)


async def calculate_score_over_added_channel(
    client: TelegramClient,
    channel: Any,
    *,
    query: str = "",
    full_info: Any = None,
) -> Dict[str, Any]:
    q = str(query or "").strip()
    if not q:
        q = str(getattr(channel, "username", None) or getattr(channel, "title", None) or "").strip()

    signals = await collect_lidgen_signals(client, channel, full_info=full_info)
    score = score_channel_for_lidgen(signals=signals, query=q, depth=0, source="search")
    return {
        "score": score.score_total,
        "score_breakdown": score.breakdown,
        "score_signals": score.extracted_signals,
        "score_hard_flags": dict(score.hard_flags),
    }


def _extract_full_channel_meta(full_chat: Any) -> Dict[str, Any]:
    """Поля из ChannelFull (`full_info.full_chat`).

    Дополняют базовые метаданные канала; в discovery эти данные не
    подтягиваются, чтобы не делать GetFullChannelRequest на каждый
    кандидат.
    """
    if full_chat is None:
        return {}
    participants: Optional[int] = getattr(full_chat, "participants_count", None)
    return {
        "about": getattr(full_chat, "about", None),
        "online_count": getattr(full_chat, "online_count", None),
        "participants_count_full": participants,
        "admins_count": getattr(full_chat, "admins_count", None),
        "kicked_count": getattr(full_chat, "kicked_count", None),
        "banned_count": getattr(full_chat, "banned_count", None),
        "linked_chat_id": getattr(full_chat, "linked_chat_id", None),
        "slowmode_seconds": getattr(full_chat, "slowmode_seconds", None),
        "pinned_msg_id": getattr(full_chat, "pinned_msg_id", None),
        "read_inbox_max_id": getattr(full_chat, "read_inbox_max_id", None),
    }


async def add_channel_via_link(client: TelegramClient, link: str) -> Dict[str, Any]:
    target = await resolve_listen_target(client, link, join=True)
    source = target.source_entity
    channel_name = normalize_chat_ref(link)

    full_info = target.full_info
    if full_info is None and isinstance(source, types.Channel):
        full_info = await client(functions.channels.GetFullChannelRequest(channel=source))

    chat = getattr(full_info, "chats", [source])[0] if full_info is not None else source
    full_chat = getattr(full_info, "full_chat", None) if full_info is not None else None

    score_payload = await calculate_score_over_added_channel(
        client, chat, query=str(channel_name), full_info=full_info
    )
    chat_meta = extract_channel_meta(chat)
    full_meta = _extract_full_channel_meta(full_chat)

    payload: Dict[str, Any] = {
        "peer_id": target.source_peer_id,
        "source_peer_id": target.source_peer_id,
        "listen_chat_id": target.listen_peer_id,
        "entity_kind": target.entity_kind,
        "listen_mode": target.listen_mode,
        "source_joined": target.source_joined,
        "listen_joined": target.listen_joined,
        "has_listen_access": target.has_listen_access,
        "access_note": target.access_note,
        "title": target.title or getattr(chat, "title", "") or "",
        "username": target.username or getattr(chat, "username", None),
        "participants_count": getattr(chat, "participants_count", None)
        or full_meta.get("participants_count_full"),
        "depth": 0,
        "source": "search",
        "recommended_by": None,
        "score": score_payload["score"],
        "score_breakdown": score_payload["score_breakdown"],
        "score_signals": score_payload["score_signals"],
        "score_hard_flags": score_payload.get("score_hard_flags") or {},
    }
    payload.update(chat_meta)
    full_meta.pop("participants_count_full", None)
    payload.update(full_meta)
    if target.linked_chat_id is not None:
        payload["linked_chat_id"] = target.linked_chat_id
    return payload

