"""Сбор сигналов для лидген-скоринга через Telethon (GetFullChannel, iter_messages, GetParticipants)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl import functions, types

from discovery_api.config import get_lidgen_members_sample_limit, get_lidgen_recent_posts_limit

log = logging.getLogger(__name__)


def _msg_reactions_total(msg: Any) -> int:
    r = getattr(msg, "reactions", None)
    if r is None:
        return 0
    results = getattr(r, "results", None) or []
    s = 0
    for rc in results:
        try:
            s += int(getattr(rc, "count", 0) or 0)
        except (TypeError, ValueError):
            continue
    return max(0, s)


def _msg_reaction_dominance(msg: Any) -> float:
    r = getattr(msg, "reactions", None)
    if r is None:
        return 0.0
    results = getattr(r, "results", None) or []
    counts: List[int] = []
    for rc in results:
        try:
            counts.append(int(getattr(rc, "count", 0) or 0))
        except (TypeError, ValueError):
            continue
    if not counts or sum(counts) <= 0:
        return 0.0
    return max(counts) / sum(counts)


def _msg_replies_count(msg: Any) -> int:
    rep = getattr(msg, "replies", None)
    if rep is None:
        return 0
    try:
        return int(getattr(rep, "replies", 0) or 0)
    except (TypeError, ValueError):
        return 0


async def _flood_sleep_retry(coro_factory, *, label: str, errors: List[str]) -> Any:
    try:
        return await coro_factory()
    except FloodWaitError as e:
        sec = int(getattr(e, "seconds", 1) or 1)
        log.warning("FloodWait %ss при %s, повтор", sec, label)
        await asyncio.sleep(sec)
        try:
            return await coro_factory()
        except Exception as ex:
            errors.append(f"{label}: FloodWait retry failed: {ex!s}")
            return None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        errors.append(f"{label}: {e!s}")
        return None


async def collect_lidgen_signals(
    client: TelegramClient,
    entity: Any,
    *,
    full_info: Any = None,
) -> Dict[str, Any]:
    """Возвращает словарь сигналов для `score_channel_for_lidgen` / `score_group_for_lidgen`."""
    errors: List[str] = []
    posts_limit = get_lidgen_recent_posts_limit()

    if isinstance(entity, types.Chat):
        from discovery_api.discovery import extract_channel_meta

        posts: List[Dict[str, Any]] = []
        try:
            async for msg in client.iter_messages(entity, limit=posts_limit):
                if getattr(msg, "action", None) is not None:
                    continue
                dt = getattr(msg, "date", None)
                ts: Optional[float] = None
                if dt is not None:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    ts = dt.timestamp()
                views = getattr(msg, "views", None)
                forwards = getattr(msg, "forwards", None)
                posts.append(
                    {
                        "date_ts": ts,
                        "views": int(views) if views is not None else None,
                        "forwards": int(forwards) if forwards is not None else None,
                        "reactions_total": _msg_reactions_total(msg),
                        "reaction_dominance": _msg_reaction_dominance(msg),
                        "replies": _msg_replies_count(msg),
                    }
                )
        except Exception as e:
            errors.append(f"iter_messages(chat): {e!s}")
        pc = getattr(entity, "participants_count", None)
        try:
            pc_i = int(pc) if pc is not None else None
        except (TypeError, ValueError):
            pc_i = None
        return {
            "title": getattr(entity, "title", "") or "",
            "username": None,
            "about": None,
            "participants_count": pc_i,
            "online_count": None,
            "linked_chat_id": None,
            "slowmode_seconds": None,
            "posts": posts,
            "members_sample": {"sampled": 0, "bots": 0, "deleted": 0},
            "meta": extract_channel_meta(entity),
            "collector_errors": errors,
        }

    members_limit = get_lidgen_members_sample_limit()

    async def _get_full():
        return await client(functions.channels.GetFullChannelRequest(channel=entity))

    if full_info is None:
        full_info = await _flood_sleep_retry(_get_full, label="GetFullChannel", errors=errors)

    full_chat = getattr(full_info, "full_chat", None) if full_info else None
    chats = getattr(full_info, "chats", None) if full_info else None
    ch = chats[0] if chats else entity

    title = getattr(ch, "title", "") or ""
    username = getattr(ch, "username", None)
    participants_entity = getattr(ch, "participants_count", None)
    participants_full = getattr(full_chat, "participants_count", None) if full_chat else None
    participants_count = participants_full if participants_full is not None else participants_entity
    if participants_count is not None:
        try:
            participants_count = int(participants_count)
        except (TypeError, ValueError):
            participants_count = None

    about = getattr(full_chat, "about", None) if full_chat else None
    online_count = getattr(full_chat, "online_count", None) if full_chat else None
    linked_chat_id = getattr(full_chat, "linked_chat_id", None) if full_chat else None
    slowmode_seconds = getattr(full_chat, "slowmode_seconds", None) if full_chat else None

    # Ленивый импорт: иначе цикл discovery → signal_collector → discovery
    from discovery_api.discovery import extract_channel_meta

    meta = extract_channel_meta(ch)

    posts: List[Dict[str, Any]] = []

    try:
        async for msg in client.iter_messages(entity, limit=posts_limit):
            if getattr(msg, "action", None) is not None:
                continue
            dt = getattr(msg, "date", None)
            ts: Optional[float] = None
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = dt.timestamp()
            views = getattr(msg, "views", None)
            forwards = getattr(msg, "forwards", None)
            posts.append(
                {
                    "date_ts": ts,
                    "views": int(views) if views is not None else None,
                    "forwards": int(forwards) if forwards is not None else None,
                    "reactions_total": _msg_reactions_total(msg),
                    "reaction_dominance": _msg_reaction_dominance(msg),
                    "replies": _msg_replies_count(msg),
                }
            )
    except FloodWaitError as e:
        sec = int(getattr(e, "seconds", 1) or 1)
        await asyncio.sleep(sec)
        try:
            async for msg in client.iter_messages(entity, limit=posts_limit):
                if getattr(msg, "action", None) is not None:
                    continue
                dt = getattr(msg, "date", None)
                ts = None
                if dt is not None:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    ts = dt.timestamp()
                views = getattr(msg, "views", None)
                forwards = getattr(msg, "forwards", None)
                posts.append(
                    {
                        "date_ts": ts,
                        "views": int(views) if views is not None else None,
                        "forwards": int(forwards) if forwards is not None else None,
                        "reactions_total": _msg_reactions_total(msg),
                        "reaction_dominance": _msg_reaction_dominance(msg),
                        "replies": _msg_replies_count(msg),
                    }
                )
        except Exception as ex:
            errors.append(f"iter_messages: {ex!s}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        errors.append(f"iter_messages: {e!s}")

    members_sample = {"sampled": 0, "bots": 0, "deleted": 0}
    is_megagroup = bool(getattr(ch, "megagroup", False)) and not bool(getattr(ch, "broadcast", False))
    if is_megagroup and getattr(ch, "access_hash", None) is not None:
        inp = types.InputChannel(int(ch.id), int(ch.access_hash))

        async def _gp():
            return await client(
                functions.channels.GetParticipantsRequest(
                    channel=inp,
                    filter=types.ChannelParticipantsRecent(),
                    offset=0,
                    limit=members_limit,
                    hash=0,
                )
            )

        part_res = await _flood_sleep_retry(_gp, label="GetParticipants", errors=errors)
        if part_res is not None:
            users = {u.id: u for u in (getattr(part_res, "users", None) or [])}
            sampled = 0
            bots = 0
            deleted = 0
            for p in getattr(part_res, "participants", None) or []:
                uid = getattr(p, "user_id", None)
                if uid is None:
                    continue
                u = users.get(uid)
                sampled += 1
                if u is None:
                    continue
                if bool(getattr(u, "bot", False)):
                    bots += 1
                if bool(getattr(u, "deleted", False)):
                    deleted += 1
            members_sample = {"sampled": sampled, "bots": bots, "deleted": deleted}

    return {
        "title": title,
        "username": username,
        "about": about,
        "participants_count": participants_count,
        "online_count": online_count,
        "linked_chat_id": linked_chat_id,
        "slowmode_seconds": slowmode_seconds,
        "posts": posts,
        "members_sample": members_sample,
        "meta": meta,
        "collector_errors": errors,
    }
