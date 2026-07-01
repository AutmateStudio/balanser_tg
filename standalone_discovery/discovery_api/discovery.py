from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import functions, types

from discovery_api.config import get_lidgen_discovery_concurrency, get_lidgen_min_score_total
from discovery_api.score_channel.lidgen_scorer import score_channel_for_lidgen, score_group_for_lidgen
from discovery_api.score_channel.signal_collector import collect_lidgen_signals


def _serialize_restriction_reasons(value: Any) -> Optional[List[Dict[str, Any]]]:
    if not value:
        return None
    items: List[Dict[str, Any]] = []
    for r in value:
        items.append(
            {
                "platform": getattr(r, "platform", None),
                "reason": getattr(r, "reason", None),
                "text": getattr(r, "text", None),
            }
        )
    return items or None


def extract_channel_meta(entity: Any) -> Dict[str, Any]:
    """Извлекает «техническую» часть метаданных Telethon-канала/группы.

    Заполняется один раз при сборе кандидата (channel/group); никаких
    дополнительных запросов в Telegram не делает. Поля, которых нет у
    конкретного объекта, ставятся в None.
    """
    date_obj = getattr(entity, "date", None)
    created_at: Optional[str] = None
    if date_obj is not None:
        try:
            created_at = date_obj.isoformat()
        except Exception:
            created_at = None
    return {
        "access_hash": getattr(entity, "access_hash", None),
        "verified": getattr(entity, "verified", None),
        "scam": getattr(entity, "scam", None),
        "fake": getattr(entity, "fake", None),
        "restricted": getattr(entity, "restricted", None),
        "megagroup": getattr(entity, "megagroup", None),
        "gigagroup": getattr(entity, "gigagroup", None),
        "broadcast": getattr(entity, "broadcast", None),
        "forum": getattr(entity, "forum", None),
        "signatures": getattr(entity, "signatures", None),
        "noforwards": getattr(entity, "noforwards", None),
        "slowmode_enabled": getattr(entity, "slowmode_enabled", None),
        "creator": getattr(entity, "creator", None),
        "has_link": getattr(entity, "has_link", None),
        "has_geo": getattr(entity, "has_geo", None),
        "join_to_send": getattr(entity, "join_to_send", None),
        "join_request": getattr(entity, "join_request", None),
        "created_at": created_at,
        "restriction_reason": _serialize_restriction_reasons(
            getattr(entity, "restriction_reason", None)
        ),
    }


@dataclass
class DiscoveredChannel:
    peer_id: int
    title: str
    username: Optional[str]
    participants_count: Optional[int]
    depth: int
    source: str
    recommended_by: Optional[int] = None
    score_total: int = 0
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    score_signals: Dict[str, Any] = field(default_factory=dict)
    score_hard_flags: Dict[str, bool] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoveryResult:
    query: str
    channels: List[DiscoveredChannel] = field(default_factory=list)
    total: int = 0
    depth_stats: Dict[int, int] = field(default_factory=dict)


@dataclass
class DiscoveredGroup:
    peer_id: int
    title: str
    username: Optional[str]
    participants_count: Optional[int]
    depth: int
    source: str
    recommended_by: Optional[int] = None
    matched_seed: Optional[str] = None
    score_total: int = 0
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    score_signals: Dict[str, Any] = field(default_factory=dict)
    score_hard_flags: Dict[str, bool] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GroupDiscoveryResult:
    query: str
    seeds: List[str] = field(default_factory=list)
    groups: List[DiscoveredGroup] = field(default_factory=list)
    total: int = 0
    depth_stats: Dict[int, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


@dataclass
class UnifiedDiscoveryResult:
    """Каналы + группы из единого поиска (POST /discover async)."""

    query: str
    channels: List[DiscoveredChannel] = field(default_factory=list)
    groups: List[DiscoveredGroup] = field(default_factory=list)
    seeds: List[str] = field(default_factory=list)
    total: int = 0
    depth_stats: Dict[int, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


async def discover_channels(
    session_string: str,
    api_id: int | str,
    api_hash: str,
    query: str,
    *,
    search_limit: int = 10,
    max_depth: int = 2,
    delay: float = 1.0,
    include_global_search: bool = True,
    include_groups: bool = False,
) -> DiscoveryResult:
    client = TelegramClient(StringSession(session_string), int(api_id), api_hash)
    await client.connect()
    try:
        return await _discover(
            client,
            query,
            search_limit,
            max_depth,
            delay,
            include_global_search=include_global_search,
            include_groups=include_groups,
        )
    finally:
        await client.disconnect()


async def discover_groups_on_client(
    client: TelegramClient,
    word: str,
    *,
    search_limit: int = 20,
    max_depth: int = 2,
    delay: float = 0.25,
    max_seeds: int = 20,
    max_runtime_sec: float = 90.0,
) -> GroupDiscoveryResult:
    """Поиск групп через уже подключённый Telethon-клиент (PG queue / clump)."""
    return await _discover_groups(
        client,
        word,
        search_limit,
        max_depth,
        delay,
        max_seeds,
        max_runtime_sec,
    )


async def discover_groups(
    session_string: str,
    api_id: int | str,
    api_hash: str,
    word: str,
    *,
    search_limit: int = 20,
    max_depth: int = 2,
    delay: float = 0.25,
    max_seeds: int = 20,
    max_runtime_sec: float = 90.0,
) -> GroupDiscoveryResult:
    client = TelegramClient(StringSession(session_string), int(api_id), api_hash)
    await client.connect()
    try:
        return await discover_groups_on_client(
            client,
            word,
            search_limit=search_limit,
            max_depth=max_depth,
            delay=delay,
            max_seeds=max_seeds,
            max_runtime_sec=max_runtime_sec,
        )
    finally:
        await client.disconnect()


def serialize_group_discovery_result(result: GroupDiscoveryResult) -> dict[str, Any]:
    return {
        "query": result.query,
        "seeds": list(result.seeds),
        "total": result.total,
        "depth_stats": {str(k): v for k, v in result.depth_stats.items()},
        "errors": list(result.errors),
        "groups": [
            {
                "peer_id": g.peer_id,
                "title": g.title,
                "username": g.username,
                "participants_count": g.participants_count,
                "depth": g.depth,
                "source": g.source,
                "recommended_by": g.recommended_by,
                "matched_seed": g.matched_seed,
                "score_total": g.score_total,
                "score_breakdown": dict(g.score_breakdown),
                "score_signals": dict(g.score_signals),
                "score_hard_flags": dict(g.score_hard_flags),
                **(g.meta or {}),
            }
            for g in result.groups
        ],
    }


def _discovered_channel_to_dict(channel: DiscoveredChannel) -> dict[str, Any]:
    return {
        "peer_id": channel.peer_id,
        "title": channel.title,
        "username": channel.username,
        "participants_count": channel.participants_count,
        "depth": channel.depth,
        "source": channel.source,
        "recommended_by": channel.recommended_by,
        "score": channel.score_total,
        "score_total": channel.score_total,
        "score_breakdown": dict(channel.score_breakdown),
        "score_signals": dict(channel.score_signals),
        "score_hard_flags": dict(channel.score_hard_flags),
        **(channel.meta or {}),
    }


def serialize_unified_discovery_result(
    result: UnifiedDiscoveryResult,
    *,
    persist: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Сериализация unified discover для payload.task_queue.result."""
    payload: dict[str, Any] = {
        "query": result.query,
        "seeds": list(result.seeds),
        "total": result.total,
        "depth_stats": {str(k): v for k, v in result.depth_stats.items()},
        "errors": list(result.errors),
        "channels": [_discovered_channel_to_dict(c) for c in result.channels],
        "groups": [
            {
                "peer_id": g.peer_id,
                "title": g.title,
                "username": g.username,
                "participants_count": g.participants_count,
                "depth": g.depth,
                "source": g.source,
                "recommended_by": g.recommended_by,
                "matched_seed": g.matched_seed,
                "score_total": g.score_total,
                "score_breakdown": dict(g.score_breakdown),
                "score_signals": dict(g.score_signals),
                "score_hard_flags": dict(g.score_hard_flags),
                **(g.meta or {}),
            }
            for g in result.groups
        ],
    }
    if persist is not None:
        payload["persist"] = persist
    return payload


async def discover_unified_on_client(
    client: TelegramClient,
    query: str,
    *,
    search_limit: int = 10,
    max_depth: int = 2,
    delay: float = 0.25,
    include_global_search: bool = True,
    include_groups: bool = True,
    max_runtime_sec: float = 90.0,
) -> UnifiedDiscoveryResult:
    """Единый поиск каналов и групп (discover + seeds group search)."""
    channel_result = await _discover(
        client,
        query,
        search_limit,
        max_depth,
        delay,
        include_global_search=include_global_search,
        include_groups=include_groups,
    )
    group_result = await _discover_groups(
        client,
        query,
        search_limit,
        max_depth,
        delay,
        max_seeds=20,
        max_runtime_sec=max_runtime_sec,
    )

    channel_peer_ids = {c.peer_id for c in channel_result.channels}
    extra_groups = [g for g in group_result.groups if g.peer_id not in channel_peer_ids]

    depth_stats = dict(channel_result.depth_stats)
    for depth, count in group_result.depth_stats.items():
        depth_stats[depth] = depth_stats.get(depth, 0) + count

    total = len(channel_result.channels) + len(extra_groups)
    return UnifiedDiscoveryResult(
        query=query,
        channels=channel_result.channels,
        groups=extra_groups,
        seeds=group_result.seeds,
        total=total,
        depth_stats=depth_stats,
        errors=list(group_result.errors),
    )


async def persist_unified_discovery(
    result: UnifiedDiscoveryResult,
    *,
    channels_repo: Any | None = None,
) -> Any:
    """Записывает отфильтрованные каналы/группы в source_channels."""
    from app_balance.queue.discover_persist import (
        PersistStats,
        build_upsert_fields,
        get_telegram_platform_id,
        should_persist_discovered,
    )
    from app_balance.queue.source_channels import SourceChannelsRepo

    repo = channels_repo or SourceChannelsRepo()
    platform_id = await get_telegram_platform_id()
    all_items: list[Any] = list(result.channels) + list(result.groups)
    stats: PersistStats = await repo.batch_upsert_discovered(
        all_items,
        platform_id=platform_id,
        should_persist=should_persist_discovered,
        build_fields=build_upsert_fields,
    )
    return stats


async def _score_discovered_channel_lidgen(
    client: TelegramClient,
    entity: Any,
    *,
    query: str,
    depth: int,
    source: str,
    recommended_by: Optional[int],
    sem: asyncio.Semaphore,
) -> DiscoveredChannel:
    async with sem:
        signals = await collect_lidgen_signals(client, entity, full_info=None)
    score = score_channel_for_lidgen(signals=signals, query=query, depth=depth, source=source)
    cid = int(getattr(entity, "id"))
    meta = dict(signals.get("meta") or {})
    return DiscoveredChannel(
        peer_id=cid,
        title=str(signals.get("title") or ""),
        username=signals.get("username"),
        participants_count=signals.get("participants_count"),
        depth=depth,
        source=source,
        recommended_by=recommended_by,
        score_total=score.score_total,
        score_breakdown=score.breakdown,
        score_signals=score.extracted_signals,
        score_hard_flags=dict(score.hard_flags),
        meta=meta,
    )


async def _discover(
    client: TelegramClient,
    query: str,
    search_limit: int,
    max_depth: int,
    delay: float,
    *,
    include_global_search: bool = True,
    include_groups: bool = False,
) -> DiscoveryResult:
    seen_ids: set[int] = set()
    all_channels: List[DiscoveredChannel] = []
    depth_stats: Dict[int, int] = {}
    sem = asyncio.Semaphore(get_lidgen_discovery_concurrency())

    queue: List[tuple[Any, int, Optional[int]]] = []

    # Источник 1: contacts.Search — каналы, у которых запрос встречается в
    # названии/username/описании. Источник 2 (опционально): messages.SearchGlobal
    # — broadcast-каналы, где запрос реально звучит в постах/обсуждениях. Второй
    # источник стоит ровно +1 запрос к Telegram на весь /discover. При
    # include_groups=True оба источника дополнительно возвращают группы/чаты.
    initial: List[Tuple[Any, str]] = [
        (ch, "search") for ch in await _search_channels(client, query, search_limit, include_groups=include_groups)
    ]
    if include_global_search:
        initial += [
            (ch, "global_search")
            for ch in await _search_channels_global(client, query, search_limit, include_groups=include_groups)
        ]

    to_score: List[Tuple[Any, str]] = []
    for ch, src in initial:
        cid = ch.id
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        to_score.append((ch, src))

    if to_score:
        scored = await asyncio.gather(
            *[
                _score_discovered_channel_lidgen(
                    client,
                    ch,
                    query=query,
                    depth=0,
                    source=src,
                    recommended_by=None,
                    sem=sem,
                )
                for ch, src in to_score
            ]
        )
        for (ch, _src), dc in zip(to_score, scored):
            all_channels.append(dc)
            depth_stats[0] = depth_stats.get(0, 0) + 1
            if max_depth >= 1:
                queue.append((ch, 0, dc.peer_id))

    while queue:
        entity, current_depth, parent_id = queue.pop(0)
        next_depth = current_depth + 1
        if next_depth > max_depth:
            continue
        recs = await _get_channel_recommendations(client, entity, include_groups=include_groups)
        if delay > 0:
            await asyncio.sleep(delay)
        new_recs = [rec for rec in recs if rec.id not in seen_ids]
        for rec in new_recs:
            seen_ids.add(rec.id)
        if not new_recs:
            continue
        batch = await asyncio.gather(
            *[
                _score_discovered_channel_lidgen(
                    client,
                    rec,
                    query=query,
                    depth=next_depth,
                    source="recommendation",
                    recommended_by=parent_id,
                    sem=sem,
                )
                for rec in new_recs
            ]
        )
        for rec, dc in zip(new_recs, batch):
            all_channels.append(dc)
            depth_stats[next_depth] = depth_stats.get(next_depth, 0) + 1
            if next_depth < max_depth:
                queue.append((rec, next_depth, rec.id))

    min_score_total = get_lidgen_min_score_total()
    filtered = [c for c in all_channels if int(c.score_total or 0) >= min_score_total]
    filtered_depth: Dict[int, int] = {}
    for c in filtered:
        filtered_depth[c.depth] = filtered_depth.get(c.depth, 0) + 1
    ranked = sorted(filtered, key=lambda c: (c.score_total, int(c.participants_count or 0), -c.depth), reverse=True)
    return DiscoveryResult(query=query, channels=ranked, total=len(ranked), depth_stats=filtered_depth)


def _generate_group_seeds(word: str, max_seeds: int = 20) -> List[str]:
    base = (word or "").strip()
    if not base:
        return []
    candidates = [base, f"{base} чат", f"{base} группа", f"{base} community", f"{base} group", f"{base} forum"]
    uniq = list(dict.fromkeys(candidates))
    return uniq[: max(1, min(int(max_seeds), 60))]


def _is_group_entity(entity: Any) -> bool:
    # Channel-based группы — супергруппы и гигагруппы (без broadcast-каналов)
    if isinstance(entity, types.Channel):
        return (
            (getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False))
            and not getattr(entity, "broadcast", False)
        )
    # Классические маленькие группы (тип Chat). ChatForbidden исключаем — туда нет доступа.
    if isinstance(entity, types.Chat) and not isinstance(entity, types.ChatForbidden):
        # deactivated/мигрированные классические чаты обычно превращаются в супергруппу;
        # сюда уже не имеет смысла их добавлять.
        if getattr(entity, "deactivated", False):
            return False
        if getattr(entity, "migrated_to", None) is not None:
            return False
        return True
    return False


async def _discover_groups(
    client: TelegramClient,
    word: str,
    search_limit: int,
    max_depth: int,
    delay: float,
    max_seeds: int,
    max_runtime_sec: float,
) -> GroupDiscoveryResult:
    seen_ids: set[int] = set()
    all_groups: List[DiscoveredGroup] = []
    depth_stats: Dict[int, int] = {}
    queue: List[tuple[Any, int, Optional[int], Optional[str]]] = []
    seeds = _generate_group_seeds(word, max_seeds)
    started_at = time.monotonic()
    errors: List[str] = []
    sem = asyncio.Semaphore(get_lidgen_discovery_concurrency())

    def out_of_time() -> bool:
        return (time.monotonic() - started_at) >= max_runtime_sec

    async def add_group(entity: Any, depth: int, source: str, parent_id: Optional[int] = None, seed: Optional[str] = None) -> None:
        if not _is_group_entity(entity):
            return
        gid = entity.id
        if gid in seen_ids:
            return
        seen_ids.add(gid)
        if out_of_time():
            signals: Dict[str, Any] = {
                "title": getattr(entity, "title", "") or "",
                "username": getattr(entity, "username", None),
                "about": None,
                "participants_count": getattr(entity, "participants_count", None),
                "online_count": None,
                "linked_chat_id": None,
                "slowmode_seconds": None,
                "posts": [],
                "members_sample": {"sampled": 0, "bots": 0, "deleted": 0},
                "meta": extract_channel_meta(entity),
                "collector_errors": ["timeout_skip_signals"],
            }
        else:
            async with sem:
                signals = await collect_lidgen_signals(client, entity, full_info=None)
        score = score_group_for_lidgen(
            signals=signals,
            query=word,
            matched_seed=seed,
            depth=depth,
            source=source,
        )
        all_groups.append(
            DiscoveredGroup(
                peer_id=gid,
                title=getattr(entity, "title", "") or "",
                username=getattr(entity, "username", None),
                participants_count=signals.get("participants_count") or getattr(entity, "participants_count", None),
                depth=depth,
                source=source,
                recommended_by=parent_id,
                matched_seed=seed,
                score_total=score.score_total,
                score_breakdown=score.breakdown,
                score_signals=score.extracted_signals,
                score_hard_flags=dict(score.hard_flags),
                meta=signals.get("meta") or extract_channel_meta(entity),
            )
        )
        depth_stats[depth] = depth_stats.get(depth, 0) + 1
        if depth < max_depth:
            queue.append((entity, depth, gid, seed))

    for seed in seeds:
        if out_of_time():
            errors.append("Превышено max_runtime_sec на этапе seeds-поиска")
            break
        contacts_entities, contacts_err = await _search_groups_contacts(client, seed, search_limit)
        if contacts_err:
            errors.append(contacts_err)
        for ent in contacts_entities:
            await add_group(ent, 0, "contacts_search", seed=seed)
        if delay > 0:
            await asyncio.sleep(delay)
        global_entities, global_err = await _search_groups_global(client, seed, search_limit)
        if global_err:
            errors.append(global_err)
        for ent in global_entities:
            await add_group(ent, 0, "global_search", seed=seed)
        if delay > 0:
            await asyncio.sleep(delay)

    while queue:
        if out_of_time():
            errors.append("Превышено max_runtime_sec на этапе обхода рекомендаций")
            break
        entity, current_depth, parent_id, seed = queue.pop(0)
        next_depth = current_depth + 1
        if next_depth > max_depth:
            continue
        recs, rec_err = await _get_group_recommendations(client, entity)
        if rec_err:
            errors.append(rec_err)
        for rec in recs:
            await add_group(rec, next_depth, "recommendation", parent_id=parent_id, seed=seed)
        if delay > 0:
            await asyncio.sleep(delay)

    ranked = sorted(all_groups, key=lambda g: (g.score_total, int(g.participants_count or 0), -g.depth), reverse=True)
    return GroupDiscoveryResult(
        query=word,
        seeds=seeds,
        groups=ranked,
        total=len(ranked),
        depth_stats=depth_stats,
        errors=errors,
    )


def _is_discoverable_entity(entity: Any, *, include_groups: bool) -> bool:
    """Подходит ли сущность для выдачи /discover.

    Всегда пропускаем broadcast-каналы. При `include_groups=True` дополнительно
    пропускаем группы/супергруппы/классические чаты (по правилам
    `_is_group_entity`, который отсекает ChatForbidden, deactivated и
    мигрированные чаты).
    """
    if isinstance(entity, types.Channel) and getattr(entity, "broadcast", False):
        return True
    if include_groups:
        return _is_group_entity(entity)
    return False


async def _search_channels(
    client: TelegramClient, query: str, limit: int, *, include_groups: bool = False
) -> List[Any]:
    try:
        res = await client(functions.contacts.SearchRequest(q=query, limit=limit))
        return [ch for ch in (getattr(res, "chats", []) or []) if _is_discoverable_entity(ch, include_groups=include_groups)]
    except Exception:
        return []


async def _search_channels_global(
    client: TelegramClient, query: str, limit: int, *, include_groups: bool = False
) -> List[Any]:
    """Ищет каналы (и опционально группы) по тексту сообщений (messages.SearchGlobal).

    В отличие от contacts.Search, который матчит только название/username канала,
    SearchGlobal находит сущности, где запрос звучит в самих постах и привязанных
    обсуждениях — то есть там, где живут потенциальные клиенты. Стоит +1 запрос
    к Telegram на весь /discover (а не на каждый канал). По умолчанию возвращаются
    только broadcast-каналы; при `include_groups=True` добавляются группы/megagroup.
    """
    try:
        res = await client(
            functions.messages.SearchGlobalRequest(
                q=query,
                filter=types.InputMessagesFilterEmpty(),
                min_date=None,
                max_date=None,
                offset_rate=0,
                offset_peer=types.InputPeerEmpty(),
                offset_id=0,
                limit=limit,
            )
        )
        return [
            ch
            for ch in (getattr(res, "chats", []) or [])
            if _is_discoverable_entity(ch, include_groups=include_groups)
        ]
    except Exception:
        return []


async def _get_channel_recommendations(
    client: TelegramClient, entity: Any, *, include_groups: bool = False
) -> List[Any]:
    if not isinstance(entity, types.Channel):
        return []
    try:
        inp = await client.get_input_entity(entity)
        res = await client(functions.channels.GetChannelRecommendationsRequest(channel=inp))
        return [ch for ch in (getattr(res, "chats", []) or []) if _is_discoverable_entity(ch, include_groups=include_groups)]
    except Exception:
        return []


async def _search_groups_contacts(
    client: TelegramClient, query: str, limit: int
) -> Tuple[List[Any], Optional[str]]:
    try:
        res = await client(functions.contacts.SearchRequest(q=query, limit=limit))
        return (
            [ch for ch in (getattr(res, "chats", []) or []) if _is_group_entity(ch)],
            None,
        )
    except Exception as e:
        return [], f"contacts.Search('{query}'): {e!s}"


async def _search_groups_global(
    client: TelegramClient, query: str, limit: int
) -> Tuple[List[Any], Optional[str]]:
    try:
        res = await client(
            functions.messages.SearchGlobalRequest(
                q=query,
                filter=types.InputMessagesFilterEmpty(),
                min_date=None,
                max_date=None,
                offset_rate=0,
                offset_peer=types.InputPeerEmpty(),
                offset_id=0,
                limit=limit,
            )
        )
        return (
            [ch for ch in (getattr(res, "chats", []) or []) if _is_group_entity(ch)],
            None,
        )
    except Exception as e:
        return [], f"messages.SearchGlobal('{query}'): {e!s}"


async def _get_group_recommendations(
    client: TelegramClient, entity: Any
) -> Tuple[List[Any], Optional[str]]:
    # У классических Chat нет рекомендаций — только у Channel-сущностей.
    if not isinstance(entity, types.Channel):
        return [], None
    try:
        inp = await client.get_input_entity(entity)
        res = await client(functions.channels.GetChannelRecommendationsRequest(channel=inp))
        return (
            [ch for ch in (getattr(res, "chats", []) or []) if _is_group_entity(ch)],
            None,
        )
    except Exception as e:
        return [], f"channels.GetChannelRecommendations(id={getattr(entity, 'id', None)}): {e!s}"

