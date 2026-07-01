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
from app_balance.queue.collect_pipeline import (
    ClientGetter,
    CollectContext,
    build_collect_op_executor,
    build_signals,
    default_client_getter,
)
from app_balance.queue.per_op_pipeline import OpExecutor, run_pipeline
from app_balance.queue.per_op_reading import TaskType
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.source_channels import SourceChannelsRepo
from app_balance.queue.task_queue import ClaimedTask, TaskQueueRepo

log = logging.getLogger(__name__)

PARSER_ADD_CHANNEL = "parser_add_channel"
PARSER_REMOVE_CHANNEL = "parser_remove_channel"
MOVE_CHANNEL = "move_channel"
COLLECT_EXTRA_DATA = "collect_extra_data"
UPDATE_CHANNEL = "update_channel"
DISCOVER_GROUPS = "discover_groups"  # legacy alias
TELEGRAM_DISCOVER = "telegram_discover"

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

    log.info(
        "execute_task: parser_add_channel OK task_id=%s ref=%s session=%s chat_id=%s",
        task.id,
        channel_ref,
        session_name,
        result.get("chat_id"),
    )

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


async def _execute_collect_extra_data(
    task: ClaimedTask,
    *,
    account: Account,
    task_type: TaskType,
    attempt_id: int | None,
    client_getter: ClientGetter,
    channels_repo: SourceChannelsRepo,
    queue: TaskQueueRepo,
    usage: ResourceUsageRepo,
) -> None:
    """F6 — multi-op сбор доп. данных: временный вход → сбор → выход (ТЗ §23).

    Идемпотентный per-op пайплайн (E6): ресурс списывается и прогресс
    фиксируется пошагово. Итоговые сигналы пишутся в source_channels.metadata,
    `extra_data_collected` выставляется в true.
    """
    channel_id = task.channel_id
    if channel_id is None:
        raise PermanentError(ErrorCode.INVALID_PAYLOAD, "missing channel_id")

    target = await channels_repo.get_collect_target(channel_id)
    if target is None:
        raise PermanentError(
            ErrorCode.INVALID_PAYLOAD, f"channel not found: {channel_id}"
        )
    ref = target.ref()
    if not ref:
        raise PermanentError(
            ErrorCode.INVALID_PAYLOAD, f"channel {channel_id} has no ref"
        )

    client = await client_getter(account.session_name)
    ctx = CollectContext()
    execute_op = build_collect_op_executor(client, ref, ctx)

    await execute_multi_op_pipeline(
        task,
        task_type=task_type,
        account=account,
        execute_op=execute_op,
        attempt_id=attempt_id,
        queue=queue,
        usage=usage,
    )

    await channels_repo.save_extra_data(channel_id, build_signals(ctx))


async def _execute_update_channel(
    task: ClaimedTask,
    *,
    account: Account,
    task_type: TaskType,
    attempt_id: int | None,
    client_getter: ClientGetter,
    channels_repo: SourceChannelsRepo,
    queue: TaskQueueRepo,
    usage: ResourceUsageRepo,
) -> None:
    """F7 — multi-op обновление метаданных канала (ТЗ §24).

    Тот же per-op Telethon-пайплайн, что collect_extra_data (F6): временный вход →
    сбор метаданных/сигналов → выход. Отличие — финальная запись: метаданные
    мёржатся в `source_channels.metadata`, обновляется `last_updated_at` (без
    флага `extra_data_collected`). Пайплайн идемпотентен (E6): ресурс списывается
    и прогресс фиксируется пошагово.
    """
    channel_id = task.channel_id
    if channel_id is None:
        raise PermanentError(ErrorCode.INVALID_PAYLOAD, "missing channel_id")

    target = await channels_repo.get_collect_target(channel_id)
    if target is None:
        raise PermanentError(
            ErrorCode.INVALID_PAYLOAD, f"channel not found: {channel_id}"
        )
    ref = target.ref()
    if not ref:
        raise PermanentError(
            ErrorCode.INVALID_PAYLOAD, f"channel {channel_id} has no ref"
        )

    client = await client_getter(account.session_name)
    ctx = CollectContext()
    execute_op = build_collect_op_executor(client, ref, ctx)

    await execute_multi_op_pipeline(
        task,
        task_type=task_type,
        account=account,
        execute_op=execute_op,
        attempt_id=attempt_id,
        queue=queue,
        usage=usage,
    )

    await channels_repo.save_channel_update(channel_id, build_signals(ctx))


def _parse_telegram_discover_payload(payload: dict[str, Any]) -> tuple[str, int, int, bool, bool]:
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        raise PermanentError(ErrorCode.INVALID_PAYLOAD, "missing query")

    try:
        limit = int(payload.get("first_pass_limit", payload.get("limit", 10)))
        depth = int(payload.get("similarity_depth", payload.get("depth", 2)))
    except (TypeError, ValueError) as exc:
        raise PermanentError(ErrorCode.INVALID_PAYLOAD, "invalid limit/depth") from exc

    if not (1 <= limit <= 100):
        raise PermanentError(ErrorCode.INVALID_PAYLOAD, "limit out of range")
    if not (0 <= depth <= 5):
        raise PermanentError(ErrorCode.INVALID_PAYLOAD, "depth out of range")

    include_global = bool(payload.get("include_global_search", True))
    include_groups = bool(payload.get("include_groups", True))
    return query.strip(), limit, depth, include_global, include_groups


async def _execute_telegram_discover(
    task: ClaimedTask,
    *,
    account: Account,
    client_getter: ClientGetter,
    queue: TaskQueueRepo,
    channels_repo: SourceChannelsRepo | None = None,
) -> None:
    """POST /discover async: поиск + фильтр discussion + upsert source_channels."""
    from discovery_api.discovery import (
        discover_unified_on_client,
        persist_unified_discovery,
        serialize_unified_discovery_result,
    )

    query, limit, depth, include_global, include_groups = _parse_telegram_discover_payload(
        dict(task.payload)
    )
    client = await client_getter(account.session_name)
    try:
        result = await discover_unified_on_client(
            client,
            query,
            search_limit=limit,
            max_depth=depth,
            include_global_search=include_global,
            include_groups=include_groups,
        )
    except QueueTaskError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise map_telethon_exception(exc) from exc

    repo = channels_repo or SourceChannelsRepo()
    persist_stats = await persist_unified_discovery(result, channels_repo=repo)

    merged = await queue.merge_payload(
        task.id,
        {
            "result": serialize_unified_discovery_result(
                result,
                persist=persist_stats.to_dict(),
            )
        },
    )
    if not merged:
        log.warning(
            "execute_task: не удалось записать result telegram_discover task_id=%s",
            task.id,
        )

    log.info(
        "execute_task: telegram_discover OK task_id=%s session=%s total=%s inserted=%s updated=%s skipped=%s",
        task.id,
        account.session_name,
        result.total,
        persist_stats.inserted,
        persist_stats.updated,
        persist_stats.skipped_no_discussion,
    )


async def execute_task(
    task: ClaimedTask,
    *,
    account: Account,
    task_type: TaskType | None = None,
    attempt_id: int | None = None,
    clump_getter: ClumpGetter | None = None,
    account_getter: AccountGetter | None = None,
    client_getter: ClientGetter | None = None,
    channels_repo: SourceChannelsRepo | None = None,
    queue: TaskQueueRepo | None = None,
    usage: ResourceUsageRepo | None = None,
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
    elif task.task_type_code == COLLECT_EXTRA_DATA:
        if task_type is None:
            raise PermanentError(
                ErrorCode.INVALID_PAYLOAD,
                "collect_extra_data requires task_type (multi-op)",
            )
        await _execute_collect_extra_data(
            task,
            account=account,
            task_type=task_type,
            attempt_id=attempt_id,
            client_getter=client_getter or default_client_getter(),
            channels_repo=channels_repo or SourceChannelsRepo(),
            queue=queue or TaskQueueRepo(),
            usage=usage or ResourceUsageRepo(),
        )
    elif task.task_type_code == UPDATE_CHANNEL:
        if task_type is None:
            raise PermanentError(
                ErrorCode.INVALID_PAYLOAD,
                "update_channel requires task_type (multi-op)",
            )
        await _execute_update_channel(
            task,
            account=account,
            task_type=task_type,
            attempt_id=attempt_id,
            client_getter=client_getter or default_client_getter(),
            channels_repo=channels_repo or SourceChannelsRepo(),
            queue=queue or TaskQueueRepo(),
            usage=usage or ResourceUsageRepo(),
        )
    elif task.task_type_code in (TELEGRAM_DISCOVER, DISCOVER_GROUPS):
        await _execute_telegram_discover(
            task,
            account=account,
            client_getter=client_getter or default_client_getter(),
            queue=queue or TaskQueueRepo(),
            channels_repo=channels_repo or SourceChannelsRepo(),
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
        client_getter: ClientGetter | None = None,
        channels_repo: SourceChannelsRepo | None = None,
        sync_after_add: SyncAfterAdd | None = None,
        sync_after_move: SyncAfterMove | None = None,
        sync_after_remove: SyncAfterRemove | None = None,
    ) -> None:
        self._clump_getter = clump_getter
        self._account_getter = account_getter
        self._client_getter = client_getter
        self._channels_repo = channels_repo
        self._sync_after_add = sync_after_add
        self._sync_after_move = sync_after_move
        self._sync_after_remove = sync_after_remove

    async def execute(
        self,
        task: ClaimedTask,
        *,
        account: Account,
        task_type: TaskType | None = None,
        attempt_id: int | None = None,
    ) -> None:
        await execute_task(
            task,
            account=account,
            task_type=task_type,
            attempt_id=attempt_id,
            clump_getter=self._clump_getter,
            account_getter=self._account_getter,
            client_getter=self._client_getter,
            channels_repo=self._channels_repo,
            sync_after_add=self._sync_after_add,
            sync_after_move=self._sync_after_move,
            sync_after_remove=self._sync_after_remove,
        )
