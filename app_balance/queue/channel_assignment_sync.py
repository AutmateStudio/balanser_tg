"""D7 — dual-write assigned_account_id: PG source_channels + JSON clump."""
from __future__ import annotations

import logging
import os
from typing import Any

from app_balance.queue import db
from app_balance.queue.accounts import Account
from app_balance.queue.source_channels import SourceChannelsRepo
from app_balance.queue.task_queue import ClaimedTask

log = logging.getLogger(__name__)

_default_repo = SourceChannelsRepo()


def pg_dual_write_enabled() -> bool:
    return bool(os.getenv("QUEUE_DATABASE_URL", "").strip())


async def _ensure_pool() -> bool:
    if not pg_dual_write_enabled():
        return False
    try:
        await db.init_pool()
        return True
    except Exception:
        log.warning("channel_assignment_sync: init_pool не удался", exc_info=True)
        return False


def _persist_clump_if_available(clump: Any) -> None:
    persist = getattr(clump, "_persist_safe", None)
    if not callable(persist):
        return
    try:
        persist()
    except Exception:
        log.debug(
            "channel_assignment_sync: persist clump не удался",
            exc_info=True,
        )


def _channel_ref_from_payload(task: ClaimedTask) -> str | None:
    payload = dict(task.payload or {})
    channel_ref = payload.get("channel_ref") or payload.get("channel")
    if channel_ref is None:
        return None
    ref = str(channel_ref).strip()
    return ref or None


async def _resolve_channel_id(
    task: ClaimedTask,
    repo: SourceChannelsRepo,
) -> int | None:
    """task.channel_id или поиск source_channels.id по payload.channel_ref."""
    if task.channel_id is not None:
        return task.channel_id
    channel_ref = _channel_ref_from_payload(task)
    if channel_ref is None:
        return None
    return await repo.find_id_by_ref(channel_ref)


async def _write_pg_assignment(
    *,
    task: ClaimedTask,
    account_id: int,
    repo: SourceChannelsRepo,
) -> None:
    channel_id = await _resolve_channel_id(task, repo)
    if channel_id is None:
        log.warning(
            "channel_assignment_sync: channel_id не удалось определить, "
            "PG dual-write пропущен (task_id=%s)",
            task.id,
        )
        return
    ok = await repo.set_assigned_account(channel_id, account_id)
    if not ok:
        raise RuntimeError(f"source_channel_not_found:{channel_id}")


async def sync_after_parser_add_channel(
    task: ClaimedTask,
    account: Account,
    clump: Any,
    *,
    repo: SourceChannelsRepo | None = None,
) -> None:
    channels_repo = repo or _default_repo
    if pg_dual_write_enabled() and await _ensure_pool():
        await _write_pg_assignment(
            task=task,
            account_id=account.id,
            repo=channels_repo,
        )
    _persist_clump_if_available(clump)


async def sync_after_move_channel(
    task: ClaimedTask,
    target_account: Account,
    clump: Any,
    *,
    repo: SourceChannelsRepo | None = None,
) -> None:
    channels_repo = repo or _default_repo
    if pg_dual_write_enabled() and await _ensure_pool():
        await _write_pg_assignment(
            task=task,
            account_id=target_account.id,
            repo=channels_repo,
        )
    _persist_clump_if_available(clump)


async def sync_after_parser_remove_channel(
    task: ClaimedTask,
    account: Account,
    clump: Any,
    *,
    repo: SourceChannelsRepo | None = None,
) -> None:
    channels_repo = repo or _default_repo
    if pg_dual_write_enabled() and await _ensure_pool():
        channel_id = await _resolve_channel_id(task, channels_repo)
        if channel_id is None:
            log.warning(
                "channel_assignment_sync: channel_id не удалось определить, "
                "PG clear пропущен (task_id=%s)",
                task.id,
            )
        else:
            ok = await channels_repo.clear_assigned_account(channel_id)
            if not ok:
                log.warning(
                    "channel_assignment_sync: source_channel id=%s не найден при remove (task_id=%s)",
                    channel_id,
                    task.id,
                )
    _persist_clump_if_available(clump)
