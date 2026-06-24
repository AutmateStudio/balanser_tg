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


async def _write_pg_assignment(
    *,
    task: ClaimedTask,
    account_id: int,
    repo: SourceChannelsRepo,
) -> None:
    if task.channel_id is None:
        log.warning(
            "channel_assignment_sync: channel_id отсутствует, PG dual-write пропущен (task_id=%s)",
            task.id,
        )
        return
    ok = await repo.set_assigned_account(task.channel_id, account_id)
    if not ok:
        raise RuntimeError(f"source_channel_not_found:{task.channel_id}")


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
