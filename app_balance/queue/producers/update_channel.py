"""F5 — продюсер update_channel (ТЗ §24).

Ставит задачи обновления метаданных по каналам с устаревшим last_updated_at
(приоритет самым старым), с dedup по каналу и лимитом target_queue_size типа.

Порог устаревания берётся из env UPDATE_CHANNEL_STALE_AFTER_SECONDS
(дефолт 30 дней). Продюсер cron-safe: при отсутствии/выключении типа задачи
возвращает пустой список и ничего не ставит.
"""
from __future__ import annotations

import logging
import os

from app_balance.queue.ops_catalog import UPDATE_CHANNEL
from app_balance.queue.producers.base import BaseProducer, ProduceResult
from app_balance.queue.source_channels import SourceChannelsRepo, StaleChannel
from app_balance.queue.task_queue import EnqueueInput

logger = logging.getLogger(__name__)

TASK_TYPE_CODE = UPDATE_CHANNEL
CREATED_BY = "update_channel_producer"
DEFAULT_BATCH_SIZE = 50
DEFAULT_STALE_AFTER_SECONDS = 2_592_000  # 30 дней
_STALE_AFTER_ENV = "UPDATE_CHANNEL_STALE_AFTER_SECONDS"


def _resolve_stale_after_seconds() -> int:
    raw = os.getenv(_STALE_AFTER_ENV, "").strip()
    if not raw:
        return DEFAULT_STALE_AFTER_SECONDS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "update_channel: некорректный %s=%r — использую дефолт %s",
            _STALE_AFTER_ENV,
            raw,
            DEFAULT_STALE_AFTER_SECONDS,
        )
        return DEFAULT_STALE_AFTER_SECONDS
    if value <= 0:
        return DEFAULT_STALE_AFTER_SECONDS
    return value


class UpdateChannelProducer(BaseProducer):
    """Продюсер задач update_channel для каналов с устаревшими метаданными."""

    def __init__(
        self,
        task_queue=None,
        task_types=None,
        channels: SourceChannelsRepo | None = None,
        stale_after_seconds: int | None = None,
    ) -> None:
        super().__init__(task_queue=task_queue, task_types=task_types)
        self._channels = channels or SourceChannelsRepo()
        self._stale_after_seconds = (
            stale_after_seconds
            if stale_after_seconds is not None
            else _resolve_stale_after_seconds()
        )

    async def produce(self) -> list[ProduceResult]:
        task_type = await self._task_types.get_by_code(TASK_TYPE_CODE)
        if task_type is None or not task_type.is_enabled:
            logger.info(
                "update_channel: тип задачи отсутствует или выключен — пропуск"
            )
            return []

        capacity = await self.remaining_capacity(task_type)
        if capacity == 0:
            return []
        batch_limit = capacity if capacity is not None else DEFAULT_BATCH_SIZE

        stale = await self._channels.list_stale_for_update(
            limit=batch_limit,
            stale_after_seconds=self._stale_after_seconds,
        )
        if not stale:
            return []

        results: list[ProduceResult] = []
        for channel in stale:
            result = await self.enqueue_if_room(_build_enqueue_input(channel))
            if result.skipped_reason == "fatal_history":
                logger.warning(
                    "update_channel: канал id=%s не поставлен в очередь — "
                    "прошлая задача id=%s завершилась фатально (%s)",
                    channel.id,
                    result.existing_task_id,
                    result.fatal_error_code,
                )
            results.append(result)
        return results


def _build_enqueue_input(channel: StaleChannel) -> EnqueueInput:
    return EnqueueInput(
        task_type_code=TASK_TYPE_CODE,
        channel_id=channel.id,
        account_id=channel.account_id,
        dedup_key=f"{TASK_TYPE_CODE}:{channel.id}",
        created_by=CREATED_BY,
        payload={"channel_id": channel.id},
    )
