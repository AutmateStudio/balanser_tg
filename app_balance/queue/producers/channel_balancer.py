"""F2 — продюсер channel_balancer: выравнивание числа каналов между аккаунтами.

ТЗ §22: балансировщик следит, чтобы между аккаунтами было распределено примерно
одинаковое количество каналов. Допустимое отклонение — ±5% (по количеству каналов,
без весов). При перекосе создаются задачи move_channel с низким приоритетом.

Группировка аккаунтов берётся из in-memory реестра clump'ов (session_registry):
move_channel в адаптере переносит канал только между сессиями одного clump и
требует parser_id в payload. Поэтому продюсер запускается в процессе воркера.

dedup_key и target_queue_size обеспечиваются BaseProducer (F1).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.producers.base import BaseProducer, ProduceResult
from app_balance.queue.source_channels import SourceChannelsRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from app_balance.queue.per_op_reading import TaskTypesRepo

log = logging.getLogger(__name__)

MOVE_CHANNEL = "move_channel"
CREATED_BY = "channel_balancer"
SKEW_THRESHOLD = 0.05

# Снимок (parser_id, clump) загруженных clump'ов реестра.
ClumpsProvider = Callable[[], list[tuple[str, Any]]]


def _default_clumps_provider() -> list[tuple[str, Any]]:
    from discovery_api.session_registry import iter_clumps

    return iter_clumps()


def _dedup_key(channel_id: int, source_id: int, target_id: int) -> str:
    """ТЗ §12: move_channel:{channel_id}:{source_account_id}:{target_account_id}."""
    return f"{MOVE_CHANNEL}:{channel_id}:{source_id}:{target_id}"


class ChannelBalancerProducer(BaseProducer):
    """F2: создаёт задачи move_channel при перекосе нагрузки > ±5%."""

    def __init__(
        self,
        task_queue: TaskQueueRepo | None = None,
        task_types: TaskTypesRepo | None = None,
        *,
        accounts: AccountsRepo | None = None,
        channels: SourceChannelsRepo | None = None,
        clumps_provider: ClumpsProvider | None = None,
    ) -> None:
        super().__init__(task_queue=task_queue, task_types=task_types)
        self._accounts = accounts or AccountsRepo()
        self._channels = channels or SourceChannelsRepo()
        self._clumps_provider = clumps_provider or _default_clumps_provider

    async def produce(self) -> list[ProduceResult]:
        results: list[ProduceResult] = []
        for parser_id, clump in self._clumps_provider():
            clump_results = await self._balance_clump(parser_id, clump)
            results.extend(clump_results)
            # queue_full — глобальный лимит move_channel исчерпан, дальше нет смысла.
            if any(r.skipped_reason == "queue_full" for r in clump_results):
                break
        return results

    async def _resolve_accounts(self, clump: Any) -> dict[int, str]:
        """session_name -> account_id для аккаунтов clump, известных в PG."""
        mapping: dict[int, str] = {}
        for pc in getattr(clump, "parser_client_list", None) or []:
            session_name = getattr(pc, "session_name", None)
            if not session_name:
                continue
            account_id = await self._accounts.get_id_by_session_name(session_name)
            if account_id is None:
                log.warning(
                    "channel_balancer: аккаунт не в PG session=%s",
                    session_name,
                )
                continue
            mapping[account_id] = session_name
        return mapping

    async def _balance_clump(
        self, parser_id: str, clump: Any
    ) -> list[ProduceResult]:
        accounts = await self._resolve_accounts(clump)
        account_ids = list(accounts.keys())
        if len(account_ids) < 2:
            return []

        counts = await self._channels.count_channels_by_accounts(account_ids)
        # Аккаунты без каналов в ответ не попадают — добиваем нулями.
        counts = {aid: counts.get(aid, 0) for aid in account_ids}

        total = sum(counts.values())
        n = len(counts)
        avg = total / n
        if avg <= 0:
            return []

        high = avg * (1 + SKEW_THRESHOLD)
        low = avg * (1 - SKEW_THRESHOLD)

        results: list[ProduceResult] = []
        channel_cache: dict[int, list] = {}

        while True:
            overloaded = sorted(
                (aid for aid, c in counts.items() if c > high),
                key=lambda aid: counts[aid],
                reverse=True,
            )
            underloaded = sorted(
                (aid for aid, c in counts.items() if c < low),
                key=lambda aid: counts[aid],
            )
            if not overloaded or not underloaded:
                break

            source_id = overloaded[0]
            target_id = underloaded[0]
            if source_id == target_id:
                break

            channel = await self._next_channel(source_id, channel_cache)
            if channel is None:
                # Нет доступных каналов для переноса с этого аккаунта.
                counts[source_id] = int(low)  # исключить из overloaded
                continue

            data = EnqueueInput(
                task_type_code=MOVE_CHANNEL,
                channel_id=channel.id,
                source_account_id=source_id,
                target_account_id=target_id,
                payload={"parser_id": parser_id, "channel_ref": channel.ref()},
                dedup_key=_dedup_key(channel.id, source_id, target_id),
                created_by=CREATED_BY,
            )
            result = await self.enqueue_if_room(data)
            results.append(result)

            if result.skipped_reason == "fatal_history":
                log.warning(
                    "channel_balancer: move_channel канал id=%s %s->%s не "
                    "поставлен — прошлая задача id=%s завершилась фатально (%s)",
                    channel.id,
                    source_id,
                    target_id,
                    result.existing_task_id,
                    result.fatal_error_code,
                )

            if result.skipped_reason == "queue_full":
                break

            # И при created, и при duplicate считаем перенос запланированным —
            # обновляем модель нагрузки, чтобы не зацикливаться на той же паре.
            counts[source_id] -= 1
            counts[target_id] += 1

        return results

    async def _next_channel(self, source_id: int, cache: dict[int, list]):
        if source_id not in cache:
            cache[source_id] = await self._channels.list_channels_for_account(
                source_id, limit=100
            )
        bucket = cache[source_id]
        if not bucket:
            return None
        return bucket.pop(0)
