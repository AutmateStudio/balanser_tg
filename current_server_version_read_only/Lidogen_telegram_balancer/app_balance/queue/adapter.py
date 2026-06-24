"""D3/D4/D7 — Telethon adapter: parser_add_channel, move_channel → SessionClump."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from app_balance.queue.accounts import Account, AccountsRepo
from app_balance.queue.channel_assignment_sync import (
    sync_after_move_channel,
    sync_after_parser_add_channel,
)
from app_balance.queue.task_queue import ClaimedTask

log = logging.getLogger(__name__)

PARSER_ADD_CHANNEL = "parser_add_channel"
MOVE_CHANNEL = "move_channel"

SyncAfterAdd = Callable[[ClaimedTask, Account, Any], Awaitable[None]]
SyncAfterMove = Callable[[ClaimedTask, Account, Any], Awaitable[None]]


@runtime_checkable
class ClumpLike(Protocol):
    async def add_channel_on_session(
        self,
        session_name: str,
        raw: str,
        *,
        webhook_url: str | None = None,
    ) -> dict[str, Any]: ...

    async def move_channel(
        self,
        ref: str,
        from_session: str,
        to_session: str,
        *,
        webhook_url: str | None = None,
    ) -> dict[str, Any]: ...

    async def start(self) -> None: ...


AccountGetter = Callable[[int], Awaitable[Account | None]]
ClumpGetter = Callable[[str], ClumpLike | None]


def _parse_parser_channel_payload(
    payload: dict[str, Any],
) -> tuple[str, str, str | None]:
    parser_id = payload.get("parser_id")
    if not isinstance(parser_id, str) or not parser_id.strip():
        raise ValueError("missing parser_id")

    channel_ref = payload.get("channel_ref")
    if not isinstance(channel_ref, str) or not channel_ref.strip():
        channel_ref = payload.get("ref")
    if not isinstance(channel_ref, str) or not channel_ref.strip():
        raise ValueError("missing channel_ref")

    webhook_url = payload.get("webhook_url")
    if webhook_url is not None and not isinstance(webhook_url, str):
        raise ValueError("invalid webhook_url")
    webhook = (webhook_url or "").strip() or None

    return parser_id.strip(), channel_ref.strip(), webhook


def _default_clump_getter() -> ClumpGetter:
    from discovery_api.session_registry import get_clump

    return get_clump


def _default_account_getter() -> AccountGetter:
    repo = AccountsRepo()
    return repo.get_by_id


async def _start_clump_after_execute(*, parser_id: str, clump: ClumpLike) -> None:
    try:
        await clump.start()
    except Exception:
        log.warning(
            "execute_task: clump.start() после execute не удался (parser_id=%s)",
            parser_id,
            exc_info=True,
        )


async def _execute_parser_add_channel(
    task: ClaimedTask,
    *,
    account: Account,
    clump_getter: ClumpGetter,
    sync_after_add: SyncAfterAdd,
) -> None:
    parser_id, channel_ref, webhook_url = _parse_parser_channel_payload(dict(task.payload))
    clump = clump_getter(parser_id)
    if clump is None:
        raise RuntimeError(f"clump_not_loaded:{parser_id}")

    result = await clump.add_channel_on_session(
        account.session_name,
        channel_ref,
        webhook_url=webhook_url,
    )
    error = result.get("error")
    if error:
        raise RuntimeError(str(error))

    await sync_after_add(task, account, clump)
    await _start_clump_after_execute(parser_id=parser_id, clump=clump)


async def _execute_move_channel(
    task: ClaimedTask,
    *,
    account_getter: AccountGetter,
    clump_getter: ClumpGetter,
    sync_after_move: SyncAfterMove,
) -> None:
    source_id = task.source_account_id
    target_id = task.target_account_id
    if source_id is None or target_id is None:
        raise ValueError("missing dual account ids")

    parser_id, channel_ref, webhook_url = _parse_parser_channel_payload(dict(task.payload))

    source = await account_getter(source_id)
    if source is None:
        raise RuntimeError(f"account_not_found:{source_id}")

    target = await account_getter(target_id)
    if target is None:
        raise RuntimeError(f"account_not_found:{target_id}")

    clump = clump_getter(parser_id)
    if clump is None:
        raise RuntimeError(f"clump_not_loaded:{parser_id}")

    result = await clump.move_channel(
        channel_ref,
        source.session_name,
        target.session_name,
        webhook_url=webhook_url,
    )
    error = result.get("error")
    if error:
        raise RuntimeError(str(error))

    await sync_after_move(task, target, clump)
    await _start_clump_after_execute(parser_id=parser_id, clump=clump)


async def execute_task(
    task: ClaimedTask,
    *,
    account: Account,
    clump_getter: ClumpGetter | None = None,
    account_getter: AccountGetter | None = None,
    sync_after_add: SyncAfterAdd | None = None,
    sync_after_move: SyncAfterMove | None = None,
) -> None:
    if clump_getter is None:
        clump_getter = _default_clump_getter()
    if account_getter is None:
        account_getter = _default_account_getter()
    if sync_after_add is None:
        sync_after_add = sync_after_parser_add_channel
    if sync_after_move is None:
        sync_after_move = sync_after_move_channel

    if task.task_type_code == PARSER_ADD_CHANNEL:
        await _execute_parser_add_channel(
            task,
            account=account,
            clump_getter=clump_getter,
            sync_after_add=sync_after_add,
        )
    elif task.task_type_code == MOVE_CHANNEL:
        await _execute_move_channel(
            task,
            account_getter=account_getter,
            clump_getter=clump_getter,
            sync_after_move=sync_after_move,
        )
    else:
        raise NotImplementedError(f"unsupported task_type: {task.task_type_code}")


class ClumpTaskAdapter:
    """Drop-in замена MockTaskAdapter для staging/prod (D3/D4/D7)."""

    def __init__(
        self,
        *,
        clump_getter: ClumpGetter | None = None,
        account_getter: AccountGetter | None = None,
        sync_after_add: SyncAfterAdd | None = None,
        sync_after_move: SyncAfterMove | None = None,
    ) -> None:
        self._clump_getter = clump_getter
        self._account_getter = account_getter
        self._sync_after_add = sync_after_add
        self._sync_after_move = sync_after_move

    async def execute(self, task: ClaimedTask, *, account: Account) -> None:
        await execute_task(
            task,
            account=account,
            clump_getter=self._clump_getter,
            account_getter=self._account_getter,
            sync_after_add=self._sync_after_add,
            sync_after_move=self._sync_after_move,
        )
