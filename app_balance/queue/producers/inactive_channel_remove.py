"""F8 — продюсер parser_remove_channel для неактивных каналов на сессиях.

Канал-кандидат (PG): нет active-проекта, активность старше порога,
assigned_account_id IS NOT NULL, есть usable ref.

Перед enqueue обязательно проверяем, что канал реально есть на сессии:
live-clump (iter_clumps) или parser_jobs.json — без Telethon restore.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from app_balance.queue.ops_catalog import PARSER_REMOVE_CHANNEL
from app_balance.queue.producers.base import BaseProducer, ProduceResult
from app_balance.queue.source_channels import (
    InactiveOnSessionChannel,
    SourceChannelsRepo,
    _normalize_channel_ref_needle,
)
from app_balance.queue.task_queue import EnqueueInput

logger = logging.getLogger(__name__)

TASK_TYPE_CODE = PARSER_REMOVE_CHANNEL
CREATED_BY = "inactive_channel_remove_producer"
DEFAULT_BATCH_SIZE = 50
DEFAULT_STALE_AFTER_SECONDS = 2_592_000  # 30 суток
_STALE_AFTER_ENV = "INACTIVE_CHANNEL_REMOVE_AFTER_SECONDS"
_CLEAR_ORPHAN_ENV = "INACTIVE_CHANNEL_REMOVE_CLEAR_ORPHAN_ASSIGNED"


@dataclass(frozen=True, slots=True)
class SessionLocation:
    parser_id: str
    session_name: str


def _resolve_stale_after_seconds() -> int:
    raw = os.getenv(_STALE_AFTER_ENV, "").strip()
    if not raw:
        return DEFAULT_STALE_AFTER_SECONDS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "inactive_channel_remove: некорректный %s=%r — использую дефолт %s",
            _STALE_AFTER_ENV,
            raw,
            DEFAULT_STALE_AFTER_SECONDS,
        )
        return DEFAULT_STALE_AFTER_SECONDS
    if value <= 0:
        return DEFAULT_STALE_AFTER_SECONDS
    return value


def _clear_orphan_assigned_enabled() -> bool:
    raw = os.getenv(_CLEAR_ORPHAN_ENV, "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _dedup_key(parser_id: str, channel_ref: str) -> str:
    # Как D9 enqueue_parser_remove_channels — единый ключ dedup.
    from discovery_api.parser_functions import _normalize_channel_ref

    normalized = str(_normalize_channel_ref(channel_ref) or "")
    return f"{TASK_TYPE_CODE}:{parser_id}:{normalized}"


def _sessions_equal(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()


def _refs_match(left: str, right: str) -> bool:
    if left == right:
        return True
    return _normalize_channel_ref_needle(left) == _normalize_channel_ref_needle(right)


def resolve_owner_from_clump(clump: Any, channel_ref: str) -> str | None:
    """session_name владельца канала в live-clump (как D9)."""
    from discovery_api.queue.producer import _resolve_owner_session_name

    return _resolve_owner_session_name(clump, channel_ref)


def resolve_owner_from_job(
    job: dict[str, Any],
    channel_ref: str,
    *,
    preferred_session: str | None = None,
) -> str | None:
    """session_name владельца из записи parser_jobs (assignments / channel_list)."""
    ref = (channel_ref or "").strip()
    if not ref:
        return None

    assignments = job.get("assignments") or {}
    if isinstance(assignments, dict):
        if ref in assignments:
            return str(assignments[ref])
        for key, session_name in assignments.items():
            if _refs_match(str(key), ref):
                return str(session_name)

    channel_list = job.get("channel_list") or []
    if not isinstance(channel_list, list):
        return None
    matched = False
    for ch in channel_list:
        if _refs_match(str(ch), ref):
            matched = True
            break
    if not matched:
        return None

    session_list = [str(x) for x in (job.get("session_name_list") or [])]
    if preferred_session:
        for sn in session_list:
            if _sessions_equal(sn, preferred_session):
                return sn
    if len(session_list) == 1:
        return session_list[0]
    legacy = job.get("session_name")
    if legacy:
        return str(legacy)
    return None


def channel_present_in_jobs(channel_ref: str, jobs: list[dict[str, Any]]) -> bool:
    """Канал есть в store (assignments/channel_list), даже без однозначного owner."""
    ref = (channel_ref or "").strip()
    if not ref:
        return False
    for job in jobs:
        assignments = job.get("assignments") or {}
        if isinstance(assignments, dict):
            if ref in assignments:
                return True
            if any(_refs_match(str(key), ref) for key in assignments):
                return True
        channel_list = job.get("channel_list") or []
        if isinstance(channel_list, list) and any(
            _refs_match(str(ch), ref) for ch in channel_list
        ):
            return True
    return False


def find_on_session(
    channel_ref: str,
    *,
    clumps: list[tuple[str, Any]] | None = None,
    jobs: list[dict[str, Any]] | None = None,
    preferred_session: str | None = None,
) -> SessionLocation | None:
    """Ищет (parser_id, session_name): сначала live-clump, иначе parser_jobs."""
    ref = (channel_ref or "").strip()
    if not ref:
        return None

    for parser_id, clump in clumps or ():
        session_name = resolve_owner_from_clump(clump, ref)
        if session_name:
            return SessionLocation(parser_id=str(parser_id), session_name=session_name)

    for job in jobs or ():
        parser_id = str(job.get("parser_id") or "").strip()
        if not parser_id:
            continue
        session_name = resolve_owner_from_job(
            job, ref, preferred_session=preferred_session
        )
        if session_name:
            return SessionLocation(parser_id=parser_id, session_name=session_name)
    return None


def _load_live_clumps() -> list[tuple[str, Any]]:
    try:
        from discovery_api.session_registry import iter_clumps

        return list(iter_clumps())
    except Exception:  # noqa: BLE001
        logger.debug(
            "inactive_channel_remove: live-clumps недоступны",
            exc_info=True,
        )
        return []


def _load_persisted_jobs() -> list[dict[str, Any]]:
    try:
        from discovery_api.parser_store import load_persisted_jobs

        return list(load_persisted_jobs())
    except Exception:  # noqa: BLE001
        logger.warning(
            "inactive_channel_remove: не удалось прочитать parser_jobs",
            exc_info=True,
        )
        return []


class InactiveChannelRemoveProducer(BaseProducer):
    """Ставит parser_remove_channel для давно неактивных каналов на сессиях."""

    def __init__(
        self,
        task_queue=None,
        task_types=None,
        channels: SourceChannelsRepo | None = None,
        stale_after_seconds: int | None = None,
        *,
        clear_orphan_assigned: bool | None = None,
        load_clumps: Callable[[], list[tuple[str, Any]]] | None = None,
        load_jobs: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        super().__init__(task_queue=task_queue, task_types=task_types)
        self._channels = channels or SourceChannelsRepo()
        self._stale_after_seconds = (
            stale_after_seconds
            if stale_after_seconds is not None
            else _resolve_stale_after_seconds()
        )
        self._clear_orphan_assigned = (
            clear_orphan_assigned
            if clear_orphan_assigned is not None
            else _clear_orphan_assigned_enabled()
        )
        self._load_clumps = load_clumps or _load_live_clumps
        self._load_jobs = load_jobs or _load_persisted_jobs

    async def produce(self) -> list[ProduceResult]:
        task_type = await self._task_types.get_by_code(TASK_TYPE_CODE)
        if task_type is None or not task_type.is_enabled:
            logger.info(
                "inactive_channel_remove: тип задачи отсутствует или выключен — пропуск"
            )
            return []

        capacity = await self.remaining_capacity(task_type)
        if capacity == 0:
            return []
        batch_limit = capacity if capacity is not None else DEFAULT_BATCH_SIZE

        candidates = await self._channels.list_inactive_on_sessions(
            limit=batch_limit,
            stale_after_seconds=self._stale_after_seconds,
        )
        if not candidates:
            return []

        clumps = self._load_clumps()
        jobs = self._load_jobs()

        results: list[ProduceResult] = []
        for channel in candidates:
            result = await self._process_channel(channel, clumps=clumps, jobs=jobs)
            if result is not None:
                results.append(result)
        return results

    async def _process_channel(
        self,
        channel: InactiveOnSessionChannel,
        *,
        clumps: list[tuple[str, Any]],
        jobs: list[dict[str, Any]],
    ) -> ProduceResult | None:
        ref = channel.ref()
        if not ref:
            logger.warning(
                "inactive_channel_remove: пустой ref channel_id=%s — skip",
                channel.channel_id,
            )
            return None

        location = find_on_session(
            ref,
            clumps=clumps,
            jobs=jobs,
            preferred_session=channel.session_name,
        )
        if location is None:
            present = channel_present_in_jobs(ref, jobs)
            # Clear только если store прочитан (jobs не пуст) и канала там нет.
            # Пустой store → только skip (нельзя отличить от недоступного mount).
            can_clear = self._clear_orphan_assigned and bool(jobs) and not present
            logger.info(
                "inactive_channel_remove: канала нет на сессии "
                "channel_id=%s account_id=%s ref=%r present=%s can_clear=%s — skip",
                channel.channel_id,
                channel.account_id,
                ref,
                present,
                can_clear,
            )
            if can_clear:
                cleared = await self._channels.clear_assigned_account(
                    channel.channel_id
                )
                if cleared:
                    logger.info(
                        "inactive_channel_remove: очищен orphan assigned_account_id "
                        "channel_id=%s",
                        channel.channel_id,
                    )
            return None

        if not _sessions_equal(location.session_name, channel.session_name):
            logger.warning(
                "inactive_channel_remove: session mismatch channel_id=%s "
                "pg_session=%s store_session=%s parser_id=%s ref=%r — skip",
                channel.channel_id,
                channel.session_name,
                location.session_name,
                location.parser_id,
                ref,
            )
            return None

        result = await self.enqueue_if_room(
            _build_enqueue_input(channel, location)
        )
        if result.skipped_reason == "fatal_history":
            logger.warning(
                "inactive_channel_remove: канал id=%s не поставлен — "
                "прошлая задача id=%s фатально (%s)",
                channel.channel_id,
                result.existing_task_id,
                result.fatal_error_code,
            )
        return result


def _build_enqueue_input(
    channel: InactiveOnSessionChannel,
    location: SessionLocation,
) -> EnqueueInput:
    ref = channel.ref()
    return EnqueueInput(
        task_type_code=TASK_TYPE_CODE,
        channel_id=channel.channel_id,
        account_id=channel.account_id,
        dedup_key=_dedup_key(location.parser_id, ref),
        created_by=CREATED_BY,
        payload={
            "parser_id": location.parser_id,
            "channel_ref": ref,
        },
    )
