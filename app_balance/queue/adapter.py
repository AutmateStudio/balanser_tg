"""D3/D4/D7 — Telethon adapter: parser_add_channel, move_channel → SessionClump."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from app_balance.queue.accounts import Account, AccountsRepo
from app_balance.queue.channel_assignment_sync import (
    sync_after_move_channel,
    sync_after_parser_add_channel,
    sync_after_parser_remove_channel,
)
from app_balance.queue.error_codes import ErrorCode
from app_balance.queue.errors import (
    PermanentError,
    QueueTaskError,
    RetryableError,
    map_clump_error_message,
    map_telethon_exception,
)
from app_balance.queue.per_op_pipeline import OpExecutor, run_pipeline
from app_balance.queue.per_op_reading import TaskType
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import ClaimedTask, TaskQueueRepo

log = logging.getLogger(__name__)

PARSER_ADD_CHANNEL = "parser_add_channel"
PARSER_REMOVE_CHANNEL = "parser_remove_channel"
MOVE_CHANNEL = "move_channel"

SyncAfterAdd = Callable[[ClaimedTask, Account, Any], Awaitable[None]]
SyncAfterMove = Callable[[ClaimedTask, Account, Any], Awaitable[None]]
SyncAfterRemove = Callable[[ClaimedTask, Account, Any], Awaitable[None]]


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

    async def remove_channel(self, ref: str) -> bool: ...

    async def start(self) -> None: ...


AccountGetter = Callable[[int], Awaitable[Account | None]]
ClumpGetter = Callable[[str], ClumpLike | None]


def _parse_parser_channel_payload(
    payload: dict[str, Any],
) -> tuple[str, str, str | None]:
    parser_id = payload.get("parser_id")
    if not isinstance(parser_id, str) or not parser_id.strip():
        raise PermanentError(ErrorCode.INVALID_PAYLOAD, "missing parser_id")

    channel_ref = payload.get("channel_ref")
    if not isinstance(channel_ref, str) or not channel_ref.strip():
        channel_ref = payload.get("ref")
    if not isinstance(channel_ref, str) or not channel_ref.strip():
        raise PermanentError(ErrorCode.INVALID_PAYLOAD, "missing channel_ref")

    webhook_url = payload.get("webhook_url")
    if webhook_url is not None and not isinstance(webhook_url, str):
        raise PermanentError(ErrorCode.INVALID_PAYLOAD, "invalid webhook_url")
    webhook = (webhook_url or "").strip() or None

    return parser_id.strip(), channel_ref.strip(), webhook


def _session_basename(session_name: str) -> str:
    """basename без .session — каноническая форма имени аккаунта (как в PG, A10)."""
    base = (session_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    if base.endswith(".session"):
        base = base[: -len(".session")]
    return base


def _resolve_clump_session_name(clump: Any, session_name: str) -> str:
    """Сопоставляет session_name аккаунта из PG с фактическим ключом сессии в clump.

    В PG имя нормализовано до basename ('Client1'), а clump индексируется тем
    значением, что передано в /parser/start (часто полный путь '/app/sessions/Client1').
    Возвращаем точное совпадение, иначе матч по basename среди session_name_list.
    Если ничего не найдено — возвращаем исходное имя (clump вернёт понятную ошибку).
    """
    has_session = getattr(clump, "has_session", None)
    if callable(has_session) and has_session(session_name):
        return session_name

    target = _session_basename(session_name)
    for candidate in getattr(clump, "session_name_list", None) or []:
        if _session_basename(str(candidate)) == target:
            return str(candidate)
    return session_name


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
        raise RetryableError(
            ErrorCode.CLUMP_NOT_LOADED,
            f"{ErrorCode.CLUMP_NOT_LOADED}:{parser_id}",
        )

    session_name = _resolve_clump_session_name(clump, account.session_name)
    try:
        result = await clump.add_channel_on_session(
            session_name,
            channel_ref,
            webhook_url=webhook_url,
        )
    except QueueTaskError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise map_telethon_exception(exc) from exc
    error = result.get("error")
    if error:
        raise map_clump_error_message(str(error))

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
        raise PermanentError(ErrorCode.INVALID_PAYLOAD, "missing dual account ids")

    parser_id, channel_ref, webhook_url = _parse_parser_channel_payload(dict(task.payload))

    source = await account_getter(source_id)
    if source is None:
        raise PermanentError(
            ErrorCode.ACCOUNT_NOT_FOUND,
            f"{ErrorCode.ACCOUNT_NOT_FOUND}:{source_id}",
        )

    target = await account_getter(target_id)
    if target is None:
        raise PermanentError(
            ErrorCode.ACCOUNT_NOT_FOUND,
            f"{ErrorCode.ACCOUNT_NOT_FOUND}:{target_id}",
        )

    clump = clump_getter(parser_id)
    if clump is None:
        raise RetryableError(
            ErrorCode.CLUMP_NOT_LOADED,
            f"{ErrorCode.CLUMP_NOT_LOADED}:{parser_id}",
        )

    from_session = _resolve_clump_session_name(clump, source.session_name)
    to_session = _resolve_clump_session_name(clump, target.session_name)
    try:
        result = await clump.move_channel(
            channel_ref,
            from_session,
            to_session,
            webhook_url=webhook_url,
        )
    except QueueTaskError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise map_telethon_exception(exc) from exc
    error = result.get("error")
    if error:
        raise map_clump_error_message(str(error))

    await sync_after_move(task, target, clump)
    await _start_clump_after_execute(parser_id=parser_id, clump=clump)


async def _execute_parser_remove_channel(
    task: ClaimedTask,
    *,
    account: Account,
    clump_getter: ClumpGetter,
    sync_after_remove: SyncAfterRemove,
) -> None:
    parser_id, channel_ref, _webhook_url = _parse_parser_channel_payload(dict(task.payload))
    clump = clump_getter(parser_id)
    if clump is None:
        raise RetryableError(
            ErrorCode.CLUMP_NOT_LOADED,
            f"{ErrorCode.CLUMP_NOT_LOADED}:{parser_id}",
        )

    try:
        removed = await clump.remove_channel(channel_ref)
    except QueueTaskError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise map_telethon_exception(exc) from exc
    if not removed:
        log.info(
            "execute_task: канал уже отсутствует в clump ref=%s parser_id=%s",
            channel_ref,
            parser_id,
        )

    await sync_after_remove(task, account, clump)
    await _start_clump_after_execute(parser_id=parser_id, clump=clump)


async def execute_task(
    task: ClaimedTask,
    *,
    account: Account,
    clump_getter: ClumpGetter | None = None,
    account_getter: AccountGetter | None = None,
    sync_after_add: SyncAfterAdd | None = None,
    sync_after_move: SyncAfterMove | None = None,
    sync_after_remove: SyncAfterRemove | None = None,
) -> None:
    if clump_getter is None:
        clump_getter = _default_clump_getter()
    if account_getter is None:
        account_getter = _default_account_getter()
    if sync_after_add is None:
        sync_after_add = sync_after_parser_add_channel
    if sync_after_move is None:
        sync_after_move = sync_after_move_channel
    if sync_after_remove is None:
        sync_after_remove = sync_after_parser_remove_channel

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
    elif task.task_type_code == PARSER_REMOVE_CHANNEL:
        await _execute_parser_remove_channel(
            task,
            account=account,
            clump_getter=clump_getter,
            sync_after_remove=sync_after_remove,
        )
    else:
        raise PermanentError(
            ErrorCode.UNSUPPORTED_TASK_TYPE,
            f"unsupported task_type: {task.task_type_code}",
        )


async def execute_multi_op_pipeline(
    task: ClaimedTask,
    *,
    task_type: TaskType,
    account: Account,
    execute_op: OpExecutor,
    attempt_id: int | None = None,
    queue: TaskQueueRepo | None = None,
    usage: ResourceUsageRepo | None = None,
) -> None:
    """E6 — точка входа для multi-op задач (`collect_extra_data`, `update_channel`).

    Будущие adapter-ветки F6/F7 передают сюда `execute_op` (исполнение одного op
    через clump/Telethon). Пайплайн идемпотентен: при retry пропускает op до
    `payload.last_completed_step` включительно и списывает ресурс только за
    оставшиеся op (ТЗ §29). Поскольку учёт ресурса ведётся пошагово, для таких
    типов задач dispatch не должен дополнительно вызывать
    ResourceUsageRepo.record_for_task.
    """
    await run_pipeline(
        task,
        task_type=task_type,
        account_id=account.id,
        attempt_id=attempt_id,
        queue=queue or TaskQueueRepo(),
        usage=usage or ResourceUsageRepo(),
        execute_op=execute_op,
    )


class ClumpTaskAdapter:
    """Drop-in замена MockTaskAdapter для staging/prod (D3/D4/D7)."""

    def __init__(
        self,
        *,
        clump_getter: ClumpGetter | None = None,
        account_getter: AccountGetter | None = None,
        sync_after_add: SyncAfterAdd | None = None,
        sync_after_move: SyncAfterMove | None = None,
        sync_after_remove: SyncAfterRemove | None = None,
    ) -> None:
        self._clump_getter = clump_getter
        self._account_getter = account_getter
        self._sync_after_add = sync_after_add
        self._sync_after_move = sync_after_move
        self._sync_after_remove = sync_after_remove

    async def execute(self, task: ClaimedTask, *, account: Account) -> None:
        await execute_task(
            task,
            account=account,
            clump_getter=self._clump_getter,
            account_getter=self._account_getter,
            sync_after_add=self._sync_after_add,
            sync_after_move=self._sync_after_move,
            sync_after_remove=self._sync_after_remove,
        )
