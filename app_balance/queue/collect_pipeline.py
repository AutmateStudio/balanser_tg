"""F6 — per-op исполнение collect_extra_data через Telethon (ТЗ §23).

Временный вход на канал → сбор метаданных и сигналов → выход. Каждый op
пайплайна (`ops_catalog.COLLECT_EXTRA_DATA`) выполняется отдельным действием,
чтобы per-op пайплайн (E6) мог списывать ресурс и фиксировать прогресс пошагово,
а retry — продолжать с упавшего op без дублей.

Telethon импортируется лениво внутри хендлеров: модуль должен импортироваться
в unit-тестах без подключения к Telegram (клиент мокается).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app_balance.queue.errors import QueueTaskError, map_telethon_exception
from app_balance.queue.per_op_pipeline import OpExecutor, PipelineStep

logger = logging.getLogger(__name__)

# op-коды пайплайна collect_extra_data (совпадают с ops_catalog).
OP_GET_ENTITY = "get_entity"
OP_JOIN = "channels.JoinChannel"
OP_GET_FULL = "channels.GetFullChannel"
OP_ITER_MESSAGES = "iter_messages"
OP_GET_PARTICIPANTS = "channels.GetParticipants"
OP_LEAVE = "channels.LeaveChannel"

DEFAULT_RECENT_POSTS_LIMIT = 50
DEFAULT_MEMBERS_SAMPLE_LIMIT = 100
_POSTS_LIMIT_ENV = "COLLECT_RECENT_POSTS_LIMIT"
_MEMBERS_LIMIT_ENV = "COLLECT_MEMBERS_SAMPLE_LIMIT"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(slots=True)
class CollectContext:
    """Аккумулятор результатов сбора между op пайплайна."""

    entity: Any = None
    full: Any = None
    joined: bool = False
    left: bool = False
    posts: list[dict[str, Any]] = field(default_factory=list)
    members: list[dict[str, Any]] = field(default_factory=list)


ClientGetter = Callable[[str], Awaitable[Any]]


def default_client_getter() -> ClientGetter:
    """Дефолтный getter Telethon-клиента по session_name (ленивый импорт)."""

    async def _get(session_name: str) -> Any:
        from discovery_api.session_registry import get_or_create_client

        return await get_or_create_client(session_name)

    return _get


def _msg_reactions_total(msg: Any) -> int:
    r = getattr(msg, "reactions", None)
    results = getattr(r, "results", None) or [] if r is not None else []
    total = 0
    for rc in results:
        try:
            total += int(getattr(rc, "count", 0) or 0)
        except (TypeError, ValueError):
            continue
    return max(0, total)


def _msg_replies_count(msg: Any) -> int:
    rep = getattr(msg, "replies", None)
    if rep is None:
        return 0
    try:
        return int(getattr(rep, "replies", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _is_megagroup(entity: Any) -> bool:
    return bool(getattr(entity, "megagroup", False))


async def _op_get_entity(client: Any, ref: str, ctx: CollectContext) -> None:
    ctx.entity = await client.get_entity(ref)


async def _op_join(client: Any, ref: str, ctx: CollectContext) -> None:
    from telethon.tl import functions

    await client(functions.channels.JoinChannelRequest(channel=ctx.entity))
    ctx.joined = True


async def _op_get_full(client: Any, ref: str, ctx: CollectContext) -> None:
    from telethon.tl import functions

    ctx.full = await client(
        functions.channels.GetFullChannelRequest(channel=ctx.entity)
    )


async def _op_iter_messages(client: Any, ref: str, ctx: CollectContext) -> None:
    limit = _env_int(_POSTS_LIMIT_ENV, DEFAULT_RECENT_POSTS_LIMIT)
    posts: list[dict[str, Any]] = []
    async for msg in client.iter_messages(ctx.entity, limit=limit):
        if getattr(msg, "action", None) is not None:
            continue
        dt = getattr(msg, "date", None)
        ts: float | None = None
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
                "replies": _msg_replies_count(msg),
            }
        )
    ctx.posts = posts


async def _op_get_participants(client: Any, ref: str, ctx: CollectContext) -> None:
    # GetParticipants имеет смысл только для megagroup (seed §23). Для broadcast
    # пропускаем без RPC, чтобы не тратить лимит зря.
    if not _is_megagroup(ctx.entity):
        logger.debug("collect: пропуск GetParticipants (не megagroup) ref=%s", ref)
        return
    limit = _env_int(_MEMBERS_LIMIT_ENV, DEFAULT_MEMBERS_SAMPLE_LIMIT)
    members: list[dict[str, Any]] = []
    participants = await client.get_participants(ctx.entity, limit=limit)
    for user in participants or []:
        members.append(
            {
                "id": getattr(user, "id", None),
                "bot": bool(getattr(user, "bot", False)),
                "deleted": bool(getattr(user, "deleted", False)),
            }
        )
    ctx.members = members


async def _op_leave(client: Any, ref: str, ctx: CollectContext) -> None:
    from telethon.tl import functions

    await client(functions.channels.LeaveChannelRequest(channel=ctx.entity))
    ctx.left = True


_OP_HANDLERS: dict[
    str, Callable[[Any, str, CollectContext], Awaitable[None]]
] = {
    OP_GET_ENTITY: _op_get_entity,
    OP_JOIN: _op_join,
    OP_GET_FULL: _op_get_full,
    OP_ITER_MESSAGES: _op_iter_messages,
    OP_GET_PARTICIPANTS: _op_get_participants,
    OP_LEAVE: _op_leave,
}


def build_collect_op_executor(
    client: Any, ref: str, ctx: CollectContext
) -> OpExecutor:
    """Собирает execute_op для per-op пайплайна collect_extra_data."""

    async def execute_op(step: PipelineStep) -> None:
        handler = _OP_HANDLERS.get(step.op_code)
        if handler is None:
            logger.warning("collect: неизвестный op '%s' — пропуск", step.op_code)
            return
        try:
            await handler(client, ref, ctx)
        except QueueTaskError:
            raise
        except Exception as exc:  # noqa: BLE001 — маппинг в typed error (E2)
            raise map_telethon_exception(exc) from exc

    return execute_op


def _members_sample(ctx: CollectContext) -> dict[str, int]:
    bots = sum(1 for m in ctx.members if m.get("bot"))
    deleted = sum(1 for m in ctx.members if m.get("deleted"))
    return {"sampled": len(ctx.members), "bots": bots, "deleted": deleted}


def build_signals(ctx: CollectContext) -> dict[str, Any]:
    """Итоговый словарь сигналов для записи в source_channels.metadata."""
    entity = ctx.entity
    full_chat = getattr(ctx.full, "full_chat", None) if ctx.full is not None else None

    participants_count = getattr(full_chat, "participants_count", None)
    if participants_count is None:
        participants_count = getattr(entity, "participants_count", None)
    try:
        participants_count = (
            int(participants_count) if participants_count is not None else None
        )
    except (TypeError, ValueError):
        participants_count = None

    return {
        "extra_data": {
            "title": getattr(entity, "title", None),
            "username": getattr(entity, "username", None),
            "about": getattr(full_chat, "about", None) if full_chat else None,
            "participants_count": participants_count,
            "is_megagroup": _is_megagroup(entity),
            "posts": ctx.posts,
            "posts_count": len(ctx.posts),
            "members_sample": _members_sample(ctx),
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
    }
