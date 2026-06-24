"""F4 — продюсер collect_extra_data (ТЗ §23).

Ставит задачи сбора доп. данных по каналам с extra_data_collected = false,
с dedup по каналу и лимитом target_queue_size типа задачи.
"""
from __future__ import annotations

import logging

from app_balance.queue.ops_catalog import COLLECT_EXTRA_DATA
from app_balance.queue.producers.base import BaseProducer, ProduceResult
from app_balance.queue.source_channels import PendingChannel, SourceChannelsRepo
from app_balance.queue.task_queue import EnqueueInput

logger = logging.getLogger(__name__)

TASK_TYPE_CODE = COLLECT_EXTRA_DATA
CREATED_BY = "collect_extra_data_producer"
DEFAULT_BATCH_SIZE = 20


class CollectExtraDataProducer(BaseProducer):
    """Продюсер задач collect_extra_data для каналов без extra_data_collected."""

    def __init__(
        self,
        task_queue=None,
        task_types=None,
        channels: SourceChannelsRepo | None = None,
    ) -> None:
        super().__init__(task_queue=task_queue, task_types=task_types)
        self._channels = channels or SourceChannelsRepo()

    async def produce(self) -> list[ProduceResult]:
        task_type = await self._task_types.get_by_code(TASK_TYPE_CODE)
        if task_type is None or not task_type.is_enabled:
            logger.info(
                "collect_extra_data: тип задачи отсутствует или выключен — пропуск"
            )
            return []

        capacity = await self.remaining_capacity(task_type)
        if capacity == 0:
            return []
        batch_limit = capacity if capacity is not None else DEFAULT_BATCH_SIZE

        pending = await self._channels.list_pending_collect(limit=batch_limit)
        if not pending:
            return []

        results: list[ProduceResult] = []
        for channel in pending:
            result = await self.enqueue_if_room(_build_enqueue_input(channel))
            results.append(result)
        return results


def _build_enqueue_input(channel: PendingChannel) -> EnqueueInput:
    return EnqueueInput(
        task_type_code=TASK_TYPE_CODE,
        channel_id=channel.channel_id,
        account_id=channel.account_id,
        dedup_key=f"{TASK_TYPE_CODE}:{channel.channel_id}",
        created_by=CREATED_BY,
    )
