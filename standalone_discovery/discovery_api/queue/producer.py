"""D8/D9 — продюсер задач parser_add_channel / parser_remove_channel в PostgreSQL."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.source_channels import SourceChannelsRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from discovery_api.parser_functions import _normalize_channel_ref

log = logging.getLogger(__name__)

PARSER_ADD_CHANNEL = "parser_add_channel"
PARSER_REMOVE_CHANNEL = "parser_remove_channel"
CREATED_BY_ADD = "discovery_api:add-channels"
CREATED_BY_REMOVE = "discovery_api:remove-channels"


@dataclass(frozen=True, slots=True)
class EnqueueAddChannelsResult:
    task_ids: list[int]
    action_id: str


@dataclass(frozen=True, slots=True)
class EnqueueRemoveChannelsResult:
    task_ids: list[int]
    action_id: str


def _dedup_key(task_type: str, parser_id: str, channel_ref: str) -> str:
    normalized = _normalize_channel_ref(channel_ref)
    return f"{task_type}:{parser_id}:{normalized}"


def _task_id_from_enqueue(result) -> int | None:
    if result.created and result.task_id is not None:
        return int(result.task_id)
    if result.existing_task_id is not None:
        return int(result.existing_task_id)
    return None


def _resolve_owner_session_name(clump: Any, channel_ref: str) -> str | None:
    """session_name владельца канала в clump (assignments или channels list)."""
    ref = (channel_ref or "").strip()
    if not ref:
        return None

    assignments = getattr(clump, "assignments", None) or {}
    if ref in assignments:
        return str(assignments[ref])

    normalized = _normalize_channel_ref(ref)
    for key, session_name in assignments.items():
        if _normalize_channel_ref(str(key)) == normalized:
            return str(session_name)

    find_owner = getattr(clump, "_find_owner", None)
    if callable(find_owner):
        owner = find_owner(ref)
        if owner is not None:
            sn = getattr(owner, "session_name", None)
            if sn:
                return str(sn)

    for pc in getattr(clump, "parser_client_list", None) or []:
        session_name = getattr(pc, "session_name", None)
        if not session_name:
            continue
        for ch in getattr(pc, "channels", None) or []:
            ch_str = str(ch)
            if ch_str == ref or _normalize_channel_ref(ch_str) == normalized:
                return str(session_name)
    return None


async def enqueue_parser_add_channels(
    *,
    parser_id: str,
    channel_list: list[str],
    webhook_url: str | None = None,
    action_id: str,
) -> EnqueueAddChannelsResult:
    """Создаёт по одной задаче parser_add_channel на каждый канал (dedup по dedup_key)."""
    repo = TaskQueueRepo()
    channels_repo = SourceChannelsRepo()
    task_ids: list[int] = []
    wh = (webhook_url or "").strip() or None

    for raw in channel_list:
        channel_ref = (raw or "").strip()
        if not channel_ref:
            continue
        normalized = _normalize_channel_ref(channel_ref)
        if not normalized:
            log.warning(
                "enqueue_parser_add_channels: пропуск некорректного канала parser_id=%s ref=%r",
                parser_id,
                raw,
            )
            continue

        payload: dict[str, str] = {
            "parser_id": parser_id,
            "channel_ref": channel_ref,
            "action_id": action_id,
        }
        if wh:
            payload["webhook_url"] = wh

        channel_id = await channels_repo.find_id_by_ref(channel_ref)

        result = await repo.enqueue(
            EnqueueInput(
                task_type_code=PARSER_ADD_CHANNEL,
                payload=payload,
                dedup_key=_dedup_key(PARSER_ADD_CHANNEL, parser_id, channel_ref),
                created_by=CREATED_BY_ADD,
                channel_id=channel_id,
            )
        )
        task_id = _task_id_from_enqueue(result)
        if task_id is not None:
            task_ids.append(task_id)

    return EnqueueAddChannelsResult(task_ids=task_ids, action_id=action_id)


async def enqueue_parser_remove_channels(
    *,
    parser_id: str,
    channel_list: list[str],
    action_id: str,
) -> EnqueueRemoveChannelsResult:
    """Создаёт по одной задаче parser_remove_channel на канал с fixed account_id владельца."""
    from discovery_api.session_registry import get_clump

    clump = get_clump(parser_id)
    if clump is None:
        log.warning(
            "enqueue_parser_remove_channels: clump не загружен parser_id=%s",
            parser_id,
        )
        return EnqueueRemoveChannelsResult(task_ids=[], action_id=action_id)

    repo = TaskQueueRepo()
    accounts = AccountsRepo()
    channels_repo = SourceChannelsRepo()
    task_ids: list[int] = []

    for raw in channel_list:
        channel_ref = (raw or "").strip()
        if not channel_ref:
            continue
        normalized = _normalize_channel_ref(channel_ref)
        if not normalized:
            log.warning(
                "enqueue_parser_remove_channels: пропуск некорректного канала parser_id=%s ref=%r",
                parser_id,
                raw,
            )
            continue

        session_name = _resolve_owner_session_name(clump, channel_ref)
        if not session_name:
            log.warning(
                "enqueue_parser_remove_channels: канал не в clump parser_id=%s ref=%r",
                parser_id,
                channel_ref,
            )
            continue

        account_id = await accounts.get_id_by_session_name(session_name)
        if account_id is None:
            log.warning(
                "enqueue_parser_remove_channels: аккаунт не в PG session=%s parser_id=%s ref=%r",
                session_name,
                parser_id,
                channel_ref,
            )
            continue

        channel_id = await channels_repo.find_id_by_ref(channel_ref)

        payload: dict[str, str] = {
            "parser_id": parser_id,
            "channel_ref": channel_ref,
            "action_id": action_id,
        }

        result = await repo.enqueue(
            EnqueueInput(
                task_type_code=PARSER_REMOVE_CHANNEL,
                payload=payload,
                dedup_key=_dedup_key(PARSER_REMOVE_CHANNEL, parser_id, channel_ref),
                created_by=CREATED_BY_REMOVE,
                account_id=account_id,
                channel_id=channel_id,
            )
        )
        task_id = _task_id_from_enqueue(result)
        if task_id is not None:
            task_ids.append(task_id)

    return EnqueueRemoveChannelsResult(task_ids=task_ids, action_id=action_id)
