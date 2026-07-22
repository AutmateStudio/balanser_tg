"""SearchGlobal с пагинацией + contacts.Search для group-pass сидов."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from telethon import TelegramClient
from telethon.tl import functions, types

log = logging.getLogger(__name__)


def _is_channel_or_group(entity: Any) -> bool:
    if isinstance(entity, types.Channel):
        return True
    if isinstance(entity, types.Chat):
        # классический чат: не forbidden / не deactivated
        if getattr(entity, "deactivated", False):
            return False
        if getattr(entity, "migrated_to", None) is not None:
            return False
        return True
    return False


def _entity_key(entity: Any) -> Optional[int]:
    try:
        from telethon.utils import get_peer_id

        return int(get_peer_id(entity))
    except Exception:
        eid = getattr(entity, "id", None)
        return int(eid) if eid is not None else None


async def search_global_pages(
    client: TelegramClient,
    query: str,
    *,
    limit_per_page: int = 10,
    max_pages: int = 3,
) -> Tuple[List[Any], Optional[str]]:
    """messages.SearchGlobal с offset_rate / offset_peer, до max_pages страниц."""
    q = (query or "").strip()
    if not q:
        return [], None

    pages = max(1, min(int(max_pages), 4))
    per_page = max(1, min(int(limit_per_page), 50))
    found: Dict[int, Any] = {}
    offset_rate = 0
    offset_peer: Any = types.InputPeerEmpty()
    offset_id = 0
    err: Optional[str] = None

    for _page in range(pages):
        try:
            res = await client(
                functions.messages.SearchGlobalRequest(
                    q=q,
                    filter=types.InputMessagesFilterEmpty(),
                    min_date=None,
                    max_date=None,
                    offset_rate=offset_rate,
                    offset_peer=offset_peer,
                    offset_id=offset_id,
                    limit=per_page,
                )
            )
        except Exception as e:
            err = f"messages.SearchGlobal('{q}'): {e!s}"
            log.warning("%s", err)
            break

        chats = getattr(res, "chats", None) or []
        for ch in chats:
            if not _is_channel_or_group(ch):
                continue
            key = _entity_key(ch)
            if key is None or key in found:
                continue
            found[key] = ch

        messages = getattr(res, "messages", None) or []
        if not messages:
            break

        last = messages[-1]
        next_rate = getattr(last, "date", None)
        # Telethon SearchGlobal использует next_rate из ответа
        next_rate_val = getattr(res, "next_rate", None)
        if next_rate_val is not None:
            try:
                offset_rate = int(next_rate_val)
            except (TypeError, ValueError):
                break
        else:
            # fallback: останавливаемся, если Telegram не дал next_rate
            break

        peer = getattr(last, "peer_id", None)
        if peer is not None:
            try:
                offset_peer = await client.get_input_entity(peer)
            except Exception:
                offset_peer = types.InputPeerEmpty()
        offset_id = int(getattr(last, "id", 0) or 0)
        if offset_id <= 0:
            break

    return list(found.values()), err


async def search_contacts(
    client: TelegramClient,
    query: str,
    *,
    limit: int = 10,
) -> Tuple[List[Any], Optional[str]]:
    q = (query or "").strip()
    if not q:
        return [], None
    try:
        res = await client(functions.contacts.SearchRequest(q=q, limit=max(1, min(int(limit), 50))))
        chats = [
            ch
            for ch in (getattr(res, "chats", None) or [])
            if _is_channel_or_group(ch)
        ]
        return chats, None
    except Exception as e:
        return [], f"contacts.Search('{q}'): {e!s}"
