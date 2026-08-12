"""F2 — продюсер channel_balancer: выравнивание числа каналов между аккаунтами.

ТЗ §22: балансировщик следит, чтобы между аккаунтами было распределено примерно
одинаковое количество каналов. Допустимое отклонение — ±5% (по количеству каналов,
без весов). При перекосе создаются задачи move_channel с низким приоритетом.

Fleet-wide (не per-clump): аккаунты берутся из ВСЕХ загруженных clump'ов реестра
(session_registry) — move_channel в адаптере (D2*) умеет переносить канал между
сессиями РАЗНЫХ clump. "Мёртвые" аккаунты (banned/disabled/error или
is_enabled=false) — безусловно приоритетный source: их каналы должны полностью
уехать на живые аккаунты (target=0), независимо от порога ±5% — иначе уже
сбалансированная ферма никогда не подхватит каналы с забаненного аккаунта.

dedup_key и target_queue_size обеспечиваются BaseProducer (F1).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from app_balance.queue.accounts import AccountsRepo, DEAD_ACCOUNT_STATUSES
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


@dataclass(frozen=True, slots=True)
class _FleetAccount:
    account_id: int
    session_name: str
    parser_id: str
    is_dead: bool


class ChannelBalancerProducer(BaseProducer):
    """F2: создаёт задачи move_channel при перекосе нагрузки > ±5% между живыми
    аккаунтами фермы, и безусловно дренирует каналы с мёртвых аккаунтов."""

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
        fleet = await self._build_fleet()
        if not fleet:
            return []
        return await self._balance_fleet(fleet)

    async def _build_fleet(self) -> dict[int, _FleetAccount]:
        """Снимок аккаунтов по ВСЕМ загруженным clump + live/dead-классификация из PG.

        Источник parser_id — clump'ы реестра (без него нельзя построить валидный
        payload для adapter.py — исполнение всё ещё идёт через конкретный clump).
        Источник live/dead — PG accounts.status/is_enabled: это источник истины,
        используемый везде в dispatch/resource-check, а не runtime-флаги Telethon,
        которые могут отставать от факта бана/отключения.
        """
        session_to_parser: dict[str, str] = {}
        for parser_id, clump in self._clumps_provider():
            for pc in getattr(clump, "parser_client_list", None) or []:
                session_name = getattr(pc, "session_name", None)
                if not session_name:
                    continue
                existing = session_to_parser.get(session_name)
                if existing is not None and existing != parser_id:
                    log.warning(
                        "channel_balancer: session=%s числится в двух clump "
                        "(%s и %s) — использую первый",
                        session_name,
                        existing,
                        parser_id,
                    )
                    continue
                session_to_parser[session_name] = parser_id

        if not session_to_parser:
            return {}

        session_names = list(session_to_parser.keys())
        ids_by_session = await self._accounts.get_ids_by_session_names(session_names)
        if not ids_by_session:
            return {}
        states = await self._accounts.list_queue_states()

        fleet: dict[int, _FleetAccount] = {}
        for session_name, account_id in ids_by_session.items():
            parser_id = session_to_parser.get(session_name)
            if parser_id is None:
                continue
            state = states.get(session_name)
            is_dead = (
                state is None
                or state.status in DEAD_ACCOUNT_STATUSES
                or not state.is_enabled
            )
            fleet[account_id] = _FleetAccount(
                account_id=account_id,
                session_name=session_name,
                parser_id=parser_id,
                is_dead=is_dead,
            )
        return fleet

    async def _balance_fleet(
        self, fleet: dict[int, _FleetAccount]
    ) -> list[ProduceResult]:
        account_ids = list(fleet.keys())
        counts = await self._channels.count_channels_by_accounts(account_ids)
        # Аккаунты без каналов в ответ не попадают — добиваем нулями.
        counts = {aid: counts.get(aid, 0) for aid in account_ids}

        dead_ids = {aid for aid, f in fleet.items() if f.is_dead}
        live_ids = [aid for aid in account_ids if aid not in dead_ids]
        if not live_ids:
            return []  # некуда переносить — вся ферма мертва

        # "Справедливая доля" считается по ВСЕМ каналам фермы (включая те, что
        # сейчас на мёртвых аккаунтах — они всё равно уедут на живых), делённым
        # на число живых аккаунтов. Так итоговое распределение уже сходится к
        # честной доле без пересчёта avg после завершения drain.
        total_all = sum(counts.values())
        avg = total_all / len(live_ids)
        high = avg * (1 + SKEW_THRESHOLD)
        low = avg * (1 - SKEW_THRESHOLD)

        results: list[ProduceResult] = []
        channel_cache: dict[int, list] = {}

        while True:
            dead_with_channels = [aid for aid in dead_ids if counts[aid] > 0]
            if dead_with_channels:
                # Drain мёртвого аккаунта безусловен (target=0) — не зависит от
                # ±5%-порога.
                source_id = max(dead_with_channels, key=lambda a: counts[a])
                target_id = min(live_ids, key=lambda a: counts[a])
            else:
                overloaded = [aid for aid in live_ids if counts[aid] > high]
                if not overloaded:
                    break
                underloaded = [aid for aid in live_ids if counts[aid] < low]
                if not underloaded:
                    break
                source_id = max(overloaded, key=lambda a: counts[a])
                target_id = min(underloaded, key=lambda a: counts[a])

            if source_id == target_id:
                break

            channel = await self._next_channel(source_id, channel_cache)
            if channel is None:
                # Нет доступных каналов для переноса с этого аккаунта.
                counts[source_id] = 0 if source_id in dead_ids else int(low)
                continue

            data = EnqueueInput(
                task_type_code=MOVE_CHANNEL,
                channel_id=channel.id,
                source_account_id=source_id,
                target_account_id=target_id,
                payload={
                    "source_parser_id": fleet[source_id].parser_id,
                    "target_parser_id": fleet[target_id].parser_id,
                    "channel_ref": channel.ref(),
                },
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
