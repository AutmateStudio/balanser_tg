"""Оркестрация lead-intent discovery (изолирована от /discover)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl import functions, types

from discovery_api.lead_intent.cache import fetch_lead_intent_cache, is_cache_fresh
from discovery_api.lead_intent.graph import (
    extract_graph_seeds_from_messages,
    peer_id_from_entity,
)
from discovery_api.lead_intent.persist import LeadPersistStats, persist_lead_candidates
from discovery_api.lead_intent.scorer import ScoreResult, merge_comment_score, score_messages
from discovery_api.lead_intent.search import search_contacts, search_global_pages
from discovery_api.lead_intent.seeds import generate_intent_seeds, is_group_pass_seed

log = logging.getLogger(__name__)


@dataclass
class LeadCandidate:
    peer_id: int
    title: str
    username: Optional[str] = None
    participants_count: Optional[int] = None
    source: str = "search_global"
    matched_seed: Optional[str] = None
    lead_score: int = 0
    lead_probability: float = 0.0
    is_job_board: bool = False
    is_community: bool = False
    is_client_base: bool = False
    intent_hits: List[str] = field(default_factory=list)
    spam_hits: List[str] = field(default_factory=list)
    graph_seeds: List[str] = field(default_factory=list)
    is_broadcast: bool = False
    is_megagroup: bool = False
    linked_chat_id: Optional[int] = None
    from_cache: bool = False
    score_breakdown: Dict[str, Any] = field(default_factory=dict)
    entity: Any = field(default=None, repr=False)


@dataclass
class LeadDiscoveryResult:
    query: str
    seeds: List[str] = field(default_factory=list)
    candidates: List[LeadCandidate] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    persist: Optional[LeadPersistStats] = None

    @property
    def total(self) -> int:
        return len(self.candidates)


def serialize_lead_discovery_result(
    result: LeadDiscoveryResult,
    *,
    persist: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    persist_dict = persist
    if persist_dict is None and result.persist is not None:
        persist_dict = result.persist.to_dict()
    return {
        "query": result.query,
        "seeds": list(result.seeds),
        "total": result.total,
        "candidates": [
            {
                "peer_id": c.peer_id,
                "title": c.title,
                "username": c.username,
                "participants_count": c.participants_count,
                "source": c.source,
                "matched_seed": c.matched_seed,
                "lead_score": c.lead_score,
                "lead_probability": c.lead_probability,
                "is_job_board": c.is_job_board,
                "is_community": c.is_community,
                "is_client_base": c.is_client_base,
                "intent_hits": list(c.intent_hits),
                "spam_hits": list(c.spam_hits),
                "graph_seeds": list(c.graph_seeds),
                "broadcast": c.is_broadcast,
                "megagroup": c.is_megagroup,
                "linked_chat_id": c.linked_chat_id,
                "from_cache": c.from_cache,
                "score_breakdown": dict(c.score_breakdown),
            }
            for c in result.candidates
        ],
        "errors": list(result.errors),
        "persist": persist_dict,
    }


def _title_of(entity: Any) -> str:
    return str(getattr(entity, "title", None) or getattr(entity, "username", None) or "")


def _username_of(entity: Any) -> Optional[str]:
    u = getattr(entity, "username", None)
    return str(u).strip() if u else None


def _participants_of(entity: Any) -> Optional[int]:
    raw = getattr(entity, "participants_count", None)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _flood_retry(coro_factory, *, label: str, errors: List[str]) -> Any:
    try:
        return await coro_factory()
    except FloodWaitError as e:
        sec = int(getattr(e, "seconds", 1) or 1)
        log.warning("FloodWait %ss при %s", sec, label)
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


def _msg_to_dict(msg: Any) -> Dict[str, Any]:
    text = ""
    for attr in ("message", "text", "raw_text"):
        val = getattr(msg, attr, None)
        if isinstance(val, str) and val.strip():
            text = val
            break
    ext = None
    f = getattr(msg, "file", None)
    if f is not None:
        ext = getattr(f, "ext", None)
        if isinstance(ext, str):
            ext = ext if ext.startswith(".") else f".{ext}"
    return {
        "text": text,
        "file_ext": ext,
        "fwd_from": getattr(msg, "fwd_from", None),
        "id": getattr(msg, "id", None),
        "_raw": msg,
    }


async def _iter_message_dicts(
    client: TelegramClient,
    entity: Any,
    *,
    limit: int,
    errors: List[str],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    async def _collect() -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        async for msg in client.iter_messages(entity, limit=limit):
            if getattr(msg, "action", None) is not None:
                continue
            rows.append(_msg_to_dict(msg))
        return rows

    try:
        out = await _collect()
    except FloodWaitError as e:
        await asyncio.sleep(int(getattr(e, "seconds", 1) or 1))
        try:
            out = await _collect()
        except Exception as ex:
            errors.append(f"iter_messages: {ex!s}")
    except Exception as e:
        errors.append(f"iter_messages: {e!s}")
    return out


async def _get_full_channel(
    client: TelegramClient,
    entity: Any,
    *,
    errors: List[str],
) -> tuple[Any, Optional[int], Optional[str], Optional[int]]:
    """Возвращает (entity, linked_chat_id, about, participants_count)."""
    if not isinstance(entity, types.Channel):
        return entity, None, None, _participants_of(entity)
    access_hash = getattr(entity, "access_hash", None)
    if access_hash is None:
        return entity, None, None, _participants_of(entity)

    async def _call():
        return await client(
            functions.channels.GetFullChannelRequest(
                channel=types.InputChannel(int(entity.id), int(access_hash))
            )
        )

    res = await _flood_retry(_call, label="GetFullChannel", errors=errors)
    if res is None:
        return entity, None, None, _participants_of(entity)
    full = getattr(res, "full_chat", None)
    linked = getattr(full, "linked_chat_id", None) if full else None
    about = getattr(full, "about", None) if full else None
    parts = getattr(full, "participants_count", None) if full else None
    if parts is None:
        parts = _participants_of(entity)
    # chats may contain updated channel
    for ch in getattr(res, "chats", None) or []:
        if getattr(ch, "id", None) == getattr(entity, "id", None):
            entity = ch
            break
    try:
        linked_i = int(linked) if linked is not None else None
    except (TypeError, ValueError):
        linked_i = None
    try:
        parts_i = int(parts) if parts is not None else None
    except (TypeError, ValueError):
        parts_i = None
    return entity, linked_i, about, parts_i


async def _community_ratio(
    client: TelegramClient,
    entity: Any,
    *,
    niche: str,
    errors: List[str],
    sample_limit: int = 80,
) -> Optional[float]:
    if not isinstance(entity, types.Channel):
        return None
    if not bool(getattr(entity, "megagroup", False)):
        return None
    if bool(getattr(entity, "broadcast", False)):
        return None
    access_hash = getattr(entity, "access_hash", None)
    if access_hash is None:
        return None

    async def _call():
        return await client(
            functions.channels.GetParticipantsRequest(
                channel=types.InputChannel(int(entity.id), int(access_hash)),
                filter=types.ChannelParticipantsRecent(),
                offset=0,
                limit=sample_limit,
                hash=0,
            )
        )

    res = await _flood_retry(_call, label="GetParticipants", errors=errors)
    if res is None:
        return None
    users = {u.id: u for u in (getattr(res, "users", None) or [])}
    niche_l = (niche or "").strip().lower()
    markers = {"design", "designer", "дизайн", "дизайнер"}
    if niche_l:
        markers.add(niche_l)
    sampled = 0
    flagged = 0
    for p in getattr(res, "participants", None) or []:
        uid = getattr(p, "user_id", None)
        if uid is None:
            continue
        u = users.get(uid)
        if u is None:
            continue
        sampled += 1
        uname = (getattr(u, "username", None) or "").lower()
        first = (getattr(u, "first_name", None) or "").lower()
        last = (getattr(u, "last_name", None) or "").lower()
        blob = f"{uname} {first} {last}"
        is_bot = bool(getattr(u, "bot", False))
        niche_in_name = any(m in blob for m in markers if m)
        if is_bot or niche_in_name:
            flagged += 1
    if sampled <= 0:
        return None
    return flagged / sampled


async def _fetch_replies_texts(
    client: TelegramClient,
    entity: Any,
    posts: List[Dict[str, Any]],
    *,
    max_posts: int = 5,
    errors: List[str],
) -> List[Dict[str, Any]]:
    comments: List[Dict[str, Any]] = []
    if not isinstance(entity, types.Channel):
        return comments
    count = 0
    for post in posts:
        if count >= max_posts:
            break
        raw = post.get("_raw")
        msg_id = getattr(raw, "id", None) if raw is not None else post.get("id")
        if msg_id is None:
            continue
        replies_meta = getattr(raw, "replies", None) if raw is not None else None
        if replies_meta is None or int(getattr(replies_meta, "replies", 0) or 0) <= 0:
            continue
        count += 1

        async def _call(mid=int(msg_id)):
            return await client(
                functions.messages.GetRepliesRequest(
                    peer=entity,
                    msg_id=mid,
                    offset_id=0,
                    offset_date=None,
                    add_offset=0,
                    limit=20,
                    max_id=0,
                    min_id=0,
                    hash=0,
                )
            )

        res = await _flood_retry(_call, label=f"GetReplies({msg_id})", errors=errors)
        if res is None:
            continue
        for m in getattr(res, "messages", None) or []:
            if getattr(m, "action", None) is not None:
                continue
            comments.append(_msg_to_dict(m))
    return comments


async def _resolve_graph_seed(
    client: TelegramClient,
    seed: str,
    *,
    errors: List[str],
) -> Optional[Any]:
    s = (seed or "").strip()
    if not s:
        return None
    if s.startswith("@"):
        uname = s.lstrip("@")

        async def _resolve():
            return await client(functions.contacts.ResolveUsernameRequest(username=uname))

        res = await _flood_retry(_resolve, label=f"ResolveUsername({uname})", errors=errors)
        if res is None:
            return None
        chats = getattr(res, "chats", None) or []
        return chats[0] if chats else None
    if s.startswith("channel:"):
        try:
            cid = int(s.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
        try:
            return await client.get_entity(cid)
        except Exception as e:
            errors.append(f"get_entity(channel:{cid}): {e!s}")
            return None
    return None


async def _score_entity(
    client: TelegramClient,
    entity: Any,
    *,
    niche: str,
    matched_seed: Optional[str],
    source: str,
    posts_limit: int,
    force_refresh: bool,
    platform_id: Optional[int],
    errors: List[str],
    mine_graph: bool,
    max_graph_seeds: int,
) -> Optional[LeadCandidate]:
    peer_id = peer_id_from_entity(entity)
    if peer_id is None:
        return None

    # cache
    if not force_refresh and platform_id is not None:
        try:
            cached = await fetch_lead_intent_cache(
                platform_id=platform_id,
                external_channel_id=str(peer_id),
            )
        except Exception as e:
            cached = None
            errors.append(f"cache_lookup({peer_id}): {e!s}")
        if is_cache_fresh(cached):
            return LeadCandidate(
                peer_id=peer_id,
                title=_title_of(entity) or str((cached or {}).get("matched_seed") or peer_id),
                username=_username_of(entity),
                participants_count=_participants_of(entity),
                source=source,
                matched_seed=matched_seed,
                lead_score=int((cached or {}).get("lead_score") or 0),
                lead_probability=float((cached or {}).get("lead_probability") or 0.0),
                is_job_board=bool((cached or {}).get("is_job_board")),
                is_community=bool((cached or {}).get("is_community")),
                is_client_base=bool((cached or {}).get("is_client_base")),
                intent_hits=list((cached or {}).get("intent_hits") or []),
                spam_hits=list((cached or {}).get("spam_hits") or []),
                graph_seeds=list((cached or {}).get("graph_seeds") or []),
                is_broadcast=bool(getattr(entity, "broadcast", False)),
                is_megagroup=bool(getattr(entity, "megagroup", False)),
                from_cache=True,
                score_breakdown={"from_cache": True},
                entity=entity,
            )

    entity, linked_chat_id, _about, participants = await _get_full_channel(
        client, entity, errors=errors
    )
    is_broadcast = bool(getattr(entity, "broadcast", False))
    is_megagroup = bool(getattr(entity, "megagroup", False)) and not is_broadcast

    posts = await _iter_message_dicts(client, entity, limit=posts_limit, errors=errors)
    community_ratio = None
    if is_megagroup:
        community_ratio = await _community_ratio(client, entity, niche=niche, errors=errors)

    scored: ScoreResult = score_messages(
        posts,
        niche=niche,
        is_broadcast=is_broadcast,
        is_megagroup=is_megagroup,
        community_ratio=community_ratio,
    )

    if is_broadcast and linked_chat_id is not None:
        comments = await _fetch_replies_texts(client, entity, posts, errors=errors)
        if comments:
            scored = merge_comment_score(scored, comments, niche=niche)

    graph_seeds: List[str] = []
    if mine_graph and (scored.lead_score >= 30 or scored.intent_hits):
        raw_msgs = [p.get("_raw") for p in posts if p.get("_raw") is not None]
        graph_seeds = extract_graph_seeds_from_messages(
            raw_msgs,
            max_seeds=max_graph_seeds,
            exclude_usernames=[_username_of(entity)] if _username_of(entity) else None,
        )

    return LeadCandidate(
        peer_id=peer_id,
        title=_title_of(entity) or str(peer_id),
        username=_username_of(entity),
        participants_count=participants,
        source=source,
        matched_seed=matched_seed,
        lead_score=scored.lead_score,
        lead_probability=scored.lead_probability,
        is_job_board=scored.is_job_board,
        is_community=scored.is_community,
        is_client_base=scored.is_client_base,
        intent_hits=list(scored.intent_hits),
        spam_hits=list(scored.spam_hits),
        graph_seeds=graph_seeds,
        is_broadcast=is_broadcast,
        is_megagroup=is_megagroup,
        linked_chat_id=linked_chat_id,
        from_cache=False,
        score_breakdown=dict(scored.breakdown),
        entity=entity,
    )


async def run_lead_intent_on_client(
    client: TelegramClient,
    query: str,
    *,
    first_pass_limit: int = 10,
    max_seeds: int = 25,
    search_pages: int = 3,
    graph_depth: int = 1,
    max_graph_seeds: int = 30,
    min_lead_score: Optional[int] = None,
    posts_limit: int = 30,
    extra_intents: Optional[List[str]] = None,
    force_refresh_posts: bool = False,
    persist: bool = True,
    channels_repo: Any = None,
) -> LeadDiscoveryResult:
    """Полный пайплайн: сиды → SearchGlobal → скоринг → граф → persist."""
    niche = (query or "").strip()
    result = LeadDiscoveryResult(query=niche)
    if not niche:
        result.errors.append("empty query")
        return result

    pages = max(1, min(int(search_pages), 4))
    limit = max(1, min(int(first_pass_limit), 50))
    posts_lim = max(5, min(int(posts_limit), 50))
    depth = max(0, min(int(graph_depth), 2))
    max_g = max(0, min(int(max_graph_seeds), 100))

    seeds = generate_intent_seeds(niche, max_seeds=max_seeds, extra_intents=extra_intents)
    result.seeds = list(seeds)

    platform_id: Optional[int] = None
    try:
        from app_balance.queue.discover_persist import get_telegram_platform_id

        platform_id = await get_telegram_platform_id()
    except Exception as e:
        result.errors.append(f"platform_id: {e!s}")

    # peer_id → (entity, matched_seed, source)
    pending: Dict[int, tuple[Any, Optional[str], str]] = {}

    for seed in seeds:
        if is_group_pass_seed(seed, niche):
            chats, err = await search_contacts(client, seed, limit=limit)
            if err:
                result.errors.append(err)
            source = "contacts_search"
        else:
            chats, err = await search_global_pages(
                client, seed, limit_per_page=limit, max_pages=pages
            )
            if err:
                result.errors.append(err)
            source = "search_global"
        for ch in chats:
            pid = peer_id_from_entity(ch)
            if pid is None or pid in pending:
                continue
            pending[pid] = (ch, seed, source)

    scored_ids: Set[int] = set()
    graph_queue: List[str] = []
    seen_graph: Set[str] = set()

    async def _process_pending(mine_graph: bool) -> None:
        nonlocal pending
        items = list(pending.items())
        pending = {}
        for pid, (entity, matched, source) in items:
            if pid in scored_ids:
                continue
            cand = await _score_entity(
                client,
                entity,
                niche=niche,
                matched_seed=matched,
                source=source,
                posts_limit=posts_lim,
                force_refresh=force_refresh_posts,
                platform_id=platform_id,
                errors=result.errors,
                mine_graph=mine_graph and max_g > 0,
                max_graph_seeds=max_g,
            )
            if cand is None:
                continue
            scored_ids.add(pid)
            result.candidates.append(cand)
            if mine_graph:
                for gs in cand.graph_seeds:
                    key = gs.lower()
                    if key in seen_graph:
                        continue
                    seen_graph.add(key)
                    graph_queue.append(gs)

    await _process_pending(mine_graph=depth > 0)

    for round_i in range(depth):
        if not graph_queue:
            break
        batch = graph_queue[:max_g]
        graph_queue = graph_queue[max_g:]
        for seed in batch:
            entity = await _resolve_graph_seed(client, seed, errors=result.errors)
            if entity is None:
                continue
            pid = peer_id_from_entity(entity)
            if pid is None or pid in scored_ids or pid in pending:
                continue
            pending[pid] = (entity, seed, "graph")
        await _process_pending(mine_graph=(round_i + 1 < depth))

    # сортировка по score desc
    result.candidates.sort(key=lambda c: c.lead_score, reverse=True)

    if persist:
        try:
            result.persist = await persist_lead_candidates(
                result.candidates,
                min_score=min_lead_score,
                channels_repo=channels_repo,
            )
        except Exception as e:
            result.errors.append(f"persist: {e!s}")
            log.exception("lead_intent persist failed")

    return result
