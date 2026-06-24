"""D8 — продюсер задач parser_add_channel в PostgreSQL task_queue."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from discovery_api.parser_functions import _normalize_channel_ref

log = logging.getLogger(__name__)

PARSER_ADD_CHANNEL = "parser_add_channel"
CREATED_BY = "discovery_api:add-channels"


@dataclass(frozen=True, slots=True)
class EnqueueAddChannelsResult:
    task_ids: list[int]
    action_id: str


def _dedup_key(parser_id: str, channel_ref: str) -> str:
    normalized = _normalize_channel_ref(channel_ref)
    return f"{PARSER_ADD_CHANNEL}:{parser_id}:{normalized}"


def _task_id_from_enqueue(result) -> int | None:
    if result.created and result.task_id is not None:
        return int(result.task_id)
    if result.existing_task_id is not None:
        return int(result.existing_task_id)
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

        result = await repo.enqueue(
            EnqueueInput(
                task_type_code=PARSER_ADD_CHANNEL,
                payload=payload,
                dedup_key=_dedup_key(parser_id, channel_ref),
                created_by=CREATED_BY,
            )
        )
        task_id = _task_id_from_enqueue(result)
        if task_id is not None:
            task_ids.append(task_id)

    return EnqueueAddChannelsResult(task_ids=task_ids, action_id=action_id)
