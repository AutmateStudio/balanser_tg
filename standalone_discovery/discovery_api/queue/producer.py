"""D8/D9 — продюсер задач parser_add_channel / parser_remove_channel / telegram_discover."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.source_channels import SourceChannelsRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from discovery_api.parser_functions import _normalize_channel_ref

log = logging.getLogger(__name__)

PARSER_ADD_CHANNEL = "parser_add_channel"
PARSER_REMOVE_CHANNEL = "parser_remove_channel"
TELEGRAM_DISCOVER = "telegram_discover"
CREATED_BY_ADD = "discovery_api:add-channels"
CREATED_BY_REMOVE = "discovery_api:remove-channels"
CREATED_BY_DISCOVER = "discovery_api:discover"


@dataclass(frozen=True, slots=True)
class EnqueueAddChannelsResult:
    task_ids: list[int]
    action_id: str
    # B12: канал -> код фатальной ошибки прошлой попытки; новая задача НЕ
    # создана (dedup_key ранее terminal failed с постоянной причиной).
    skipped_fatal: dict[str, str] = field(default_factory=dict)
    # Канал уже слушается active+enabled аккаунтом того же clump — задача не
    # создаётся (цель уже достигнута), иначе воркер уводит её в бесконечный
    # RETRYABLE «Канал уже на другой сессии».
    skipped_in_clump: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EnqueueRemoveChannelsResult:
    task_ids: list[int]
    action_id: str


@dataclass(frozen=True, slots=True)
class EnqueueTelegramDiscoverResult:
    task_id: int | None
    action_id: str


def _dedup_key(task_type: str, parser_id: str, channel_ref: str) -> str:
    normalized = _normalize_channel_ref(channel_ref)
    return f"{task_type}:{parser_id}:{normalized}"


def _task_id_from_enqueue(result) -> int | None:
    if result.created and result.task_id is not None:
        return int(result.task_id)
    if result.existing_task_id is not None:
        return int(result.existing_task_id)
    return None


def _resolve_owner_session_name(clump: Any, channel_ref: str) -> str | None:
    """session_name владельца канала в clump (assignments или channels list)."""
    ref = (channel_ref or "").strip()
    if not ref:
        return None

    assignments = getattr(clump, "assignments", None) or {}
    if ref in assignments:
        return str(assignments[ref])

    normalized = _normalize_channel_ref(ref)
    for key, session_name in assignments.items():
        if _normalize_channel_ref(str(key)) == normalized:
            return str(session_name)

    find_owner = getattr(clump, "_find_owner", None)
    if callable(find_owner):
        owner = find_owner(ref)
        if owner is not None:
            sn = getattr(owner, "session_name", None)
            if sn:
                return str(sn)

    for pc in getattr(clump, "parser_client_list", None) or []:
        session_name = getattr(pc, "session_name", None)
        if not session_name:
            continue
        for ch in getattr(pc, "channels", None) or []:
            ch_str = str(ch)
            if ch_str == ref or _normalize_channel_ref(ch_str) == normalized:
                return str(session_name)
    return None


async def enqueue_parser_add_channels(
    *,
    parser_id: str,
    channel_list: list[str],
    webhook_url: str | None = None,
    action_id: str,
    skip_known_fatal: bool = True,
) -> EnqueueAddChannelsResult:
    """Создаёт по одной задаче parser_add_channel на каждый канал (dedup по dedup_key).

    B12: если для канала последняя попытка уже terminal failed с постоянной
    причиной (banned, channel_private, invalid_payload, ...) — новая задача
    не создаётся (см. TaskQueueRepo.find_fatal_history/FATAL_ERROR_CODES).
    Каналы, уже активные (queued/scheduled/retry/in_progress), и так не
    дублируются — это гарантирует partial unique index dedup_key.
    Источник вызова (n8n tg-parser-sync и т.п.) не меняется: он продолжает
    присылать один и тот же список каналов каждый тик, фильтрация — здесь.
    `skip_known_fatal=False` — принудительный повтор (ручной override оператора).

    Batch-путь (без N× round-trip на канал):
      1) owner-in-clump через list_by_session_names
      2) find_ids_by_refs
      3) enqueue_many (fatal-history + INSERT)
    """
    from app_balance.queue.accounts_sync import normalize_session_name
    from discovery_api.session_registry import get_clump

    repo = TaskQueueRepo()
    channels_repo = SourceChannelsRepo()
    accounts_repo = AccountsRepo()
    clump = get_clump(parser_id)
    skipped_fatal: dict[str, str] = {}
    skipped_in_clump: dict[str, str] = {}
    wh = (webhook_url or "").strip() or None

    # --- шаг 0: нормализация refs, owner session в памяти ---
    candidates: list[tuple[str, str]] = []  # (channel_ref, normalized)
    owner_by_ref: dict[str, str] = {}
    owner_sessions: list[str] = []
    seen_norm: set[str] = set()

    for raw in channel_list:
        channel_ref = (raw or "").strip()
        if not channel_ref:
            continue
        normalized = _normalize_channel_ref(channel_ref)
        if not normalized:
            log.warning(
                "enqueue_parser_add_channels: пропуск некорректного канала "
                "parser_id=%s ref=%r",
                parser_id,
                raw,
            )
            continue
        # Один dedup_key на normalized — дубли в одном запросе схлопываем.
        if normalized in seen_norm:
            continue
        seen_norm.add(normalized)
        candidates.append((channel_ref, normalized))
        owner_session = _resolve_owner_session_name(clump, channel_ref)
        if owner_session:
            owner_by_ref[channel_ref] = owner_session
            owner_sessions.append(owner_session)

    # --- шаг 1: batch owner status ---
    accounts_by_session: dict[str, Any] = {}
    if owner_sessions:
        try:
            accounts_by_session = await accounts_repo.list_by_session_names(
                owner_sessions
            )
        except Exception:  # noqa: BLE001 — PG недоступен → не блокируем enqueue
            log.warning(
                "enqueue dedup: не удалось batch-проверить владельцев clump",
                exc_info=True,
            )
            accounts_by_session = {}

    to_enqueue_refs: list[str] = []
    for channel_ref, _normalized in candidates:
        owner_session = owner_by_ref.get(channel_ref)
        if not owner_session:
            to_enqueue_refs.append(channel_ref)
            continue
        account = accounts_by_session.get(
            normalize_session_name(owner_session)
        )
        if (
            account is not None
            and account.status == "active"
            and account.is_enabled
        ):
            log.info(
                "enqueue_parser_add_channels: канал ref=%r parser_id=%s не "
                "поставлен — уже слушается active-аккаунтом clump session=%s "
                "(dedup на enqueue)",
                channel_ref,
                parser_id,
                owner_session,
            )
            skipped_in_clump[channel_ref] = owner_session
            continue
        to_enqueue_refs.append(channel_ref)

    if not to_enqueue_refs:
        return EnqueueAddChannelsResult(
            task_ids=[],
            action_id=action_id,
            skipped_fatal=skipped_fatal,
            skipped_in_clump=skipped_in_clump,
        )

    # --- шаг 2: batch channel_id lookup ---
    channel_ids = await channels_repo.find_ids_by_refs(to_enqueue_refs)

    # --- шаг 3–4: fatal-history + INSERT через enqueue_many ---
    inputs: list[EnqueueInput] = []
    for channel_ref in to_enqueue_refs:
        payload: dict[str, str] = {
            "parser_id": parser_id,
            "channel_ref": channel_ref,
            "action_id": action_id,
        }
        if wh:
            payload["webhook_url"] = wh
        inputs.append(
            EnqueueInput(
                task_type_code=PARSER_ADD_CHANNEL,
                payload=payload,
                dedup_key=_dedup_key(PARSER_ADD_CHANNEL, parser_id, channel_ref),
                created_by=CREATED_BY_ADD,
                channel_id=channel_ids.get(channel_ref),
            )
        )

    enqueue_results = await repo.enqueue_many(
        inputs, skip_known_fatal=skip_known_fatal
    )

    task_ids: list[int] = []
    for channel_ref, result in zip(to_enqueue_refs, enqueue_results):
        if result.skipped_reason == "fatal_history":
            log.warning(
                "enqueue_parser_add_channels: канал ref=%r parser_id=%s не "
                "поставлен — прошлая задача id=%s фатально завершена (%s)",
                channel_ref,
                parser_id,
                result.existing_task_id,
                result.fatal_error_code,
            )
            skipped_fatal[channel_ref] = result.fatal_error_code or "fatal"
            continue
        task_id = _task_id_from_enqueue(result)
        if task_id is not None:
            task_ids.append(task_id)

    return EnqueueAddChannelsResult(
        task_ids=task_ids,
        action_id=action_id,
        skipped_fatal=skipped_fatal,
        skipped_in_clump=skipped_in_clump,
    )


async def enqueue_parser_remove_channels(
    *,
    parser_id: str,
    channel_list: list[str],
    action_id: str,
) -> EnqueueRemoveChannelsResult:
    """Создаёт по одной задаче parser_remove_channel на канал с fixed account_id владельца.

    Batch-путь (без N× round-trip на канал), как в enqueue_parser_add_channels:
      1) owner-in-clump — в памяти (_resolve_owner_session_name)
      2) get_ids_by_session_names — один batch-запрос вместо N
      3) find_ids_by_refs — batch тиры 1–3 + fallback на остаток
      4) enqueue_many — один INSERT + lookup конфликтов
    """
    from app_balance.queue.accounts_sync import normalize_session_name
    from discovery_api.session_registry import get_clump

    clump = get_clump(parser_id)
    if clump is None:
        log.warning(
            "enqueue_parser_remove_channels: clump не загружен parser_id=%s",
            parser_id,
        )
        return EnqueueRemoveChannelsResult(task_ids=[], action_id=action_id)

    repo = TaskQueueRepo()
    accounts = AccountsRepo()
    channels_repo = SourceChannelsRepo()

    # --- шаг 0: нормализация refs, owner session в памяти ---
    owner_by_ref: dict[str, str] = {}
    seen_norm: set[str] = set()

    for raw in channel_list:
        channel_ref = (raw or "").strip()
        if not channel_ref:
            continue
        normalized = _normalize_channel_ref(channel_ref)
        if not normalized:
            log.warning(
                "enqueue_parser_remove_channels: пропуск некорректного канала "
                "parser_id=%s ref=%r",
                parser_id,
                raw,
            )
            continue
        if normalized in seen_norm:
            continue
        seen_norm.add(normalized)

        session_name = _resolve_owner_session_name(clump, channel_ref)
        if not session_name:
            log.warning(
                "enqueue_parser_remove_channels: канал не в clump parser_id=%s ref=%r",
                parser_id,
                channel_ref,
            )
            continue
        owner_by_ref[channel_ref] = session_name

    if not owner_by_ref:
        return EnqueueRemoveChannelsResult(task_ids=[], action_id=action_id)

    # --- шаг 1: batch account_id по владельцам ---
    account_ids_by_session = await accounts.get_ids_by_session_names(
        list(owner_by_ref.values())
    )

    refs_with_account: list[str] = []
    account_id_by_ref: dict[str, int] = {}
    for channel_ref, session_name in owner_by_ref.items():
        account_id = account_ids_by_session.get(
            normalize_session_name(session_name)
        )
        if account_id is None:
            log.warning(
                "enqueue_parser_remove_channels: аккаунт не в PG session=%s "
                "parser_id=%s ref=%r",
                session_name,
                parser_id,
                channel_ref,
            )
            continue
        refs_with_account.append(channel_ref)
        account_id_by_ref[channel_ref] = account_id

    if not refs_with_account:
        return EnqueueRemoveChannelsResult(task_ids=[], action_id=action_id)

    # --- шаг 2: batch channel_id lookup ---
    channel_ids = await channels_repo.find_ids_by_refs(refs_with_account)

    # --- шаг 3–4: fatal-history + INSERT через enqueue_many ---
    inputs: list[EnqueueInput] = []
    for channel_ref in refs_with_account:
        payload: dict[str, str] = {
            "parser_id": parser_id,
            "channel_ref": channel_ref,
            "action_id": action_id,
        }
        inputs.append(
            EnqueueInput(
                task_type_code=PARSER_REMOVE_CHANNEL,
                payload=payload,
                dedup_key=_dedup_key(PARSER_REMOVE_CHANNEL, parser_id, channel_ref),
                created_by=CREATED_BY_REMOVE,
                account_id=account_id_by_ref[channel_ref],
                channel_id=channel_ids.get(channel_ref),
            )
        )

    enqueue_results = await repo.enqueue_many(inputs)

    task_ids: list[int] = []
    for result in enqueue_results:
        task_id = _task_id_from_enqueue(result)
        if task_id is not None:
            task_ids.append(task_id)

    return EnqueueRemoveChannelsResult(task_ids=task_ids, action_id=action_id)


def _telegram_discover_dedup_key(
    session_name: str | None,
    query: str,
    *,
    first_pass_limit: int,
    similarity_depth: int,
    include_global_search: bool,
    include_groups: bool,
) -> str:
    """dedup_key для telegram_discover.

    session_name задан — как раньше, дедуп на конкретную сессию (fixed
    account). session_name отсутствует (auto-pick) — используется плейсхолдер
    "auto": дедуп идёт по query+параметрам без привязки к сессии, т.к.
    конкретный аккаунт ещё не выбран на момент постановки (его подберёт
    dispatch()). Один и тот же запрос без фиксированной сессии не дублируется,
    пока предыдущая попытка активна.
    """
    stripped = (session_name or "").strip()
    if stripped:
        from app_balance.queue.accounts_sync import normalize_session_name

        session = normalize_session_name(stripped)
    else:
        session = "auto"
    normalized_query = (query or "").strip().lower()
    return (
        f"{TELEGRAM_DISCOVER}:{session}:{normalized_query}:"
        f"{first_pass_limit}:{similarity_depth}:"
        f"{int(include_global_search)}:{int(include_groups)}"
    )


async def enqueue_telegram_discover(
    *,
    session_name: str | None = None,
    query: str,
    first_pass_limit: int,
    similarity_depth: int,
    include_global_search: bool,
    include_groups: bool,
    action_id: str,
) -> EnqueueTelegramDiscoverResult:
    """Ставит задачу telegram_discover.

    session_name задан — fixed account_id, резерв через dispatch (старое
    поведение, обратная совместимость).
    session_name не задан/пуст — auto-pick: account_id=None в EnqueueInput,
    dispatch() сам подберёт свободный аккаунт с достаточным ресурсом
    (та же механика _reserve_auto_pick_account, что у parser_add_channel;
    ресурс-чек по min_available_resource_percent=20% для telegram_discover,
    приоритет 80 — см. DB/A9_seed.sql).
    """
    trimmed_query = (query or "").strip()
    if not trimmed_query:
        return EnqueueTelegramDiscoverResult(task_id=None, action_id=action_id)

    session_stripped = (session_name or "").strip()
    account_id: int | None = None
    normalized_session: str | None = None

    if session_stripped:
        from app_balance.queue.accounts_sync import normalize_session_name

        accounts = AccountsRepo()
        normalized_session = normalize_session_name(session_stripped)
        account_id = await accounts.get_id_by_session_name(session_stripped)
        if account_id is None:
            log.warning(
                "enqueue_telegram_discover: аккаунт не в PG session=%r",
                session_stripped,
            )
            return EnqueueTelegramDiscoverResult(task_id=None, action_id=action_id)

    payload: dict[str, Any] = {
        "query": trimmed_query,
        "first_pass_limit": int(first_pass_limit),
        "similarity_depth": int(similarity_depth),
        "include_global_search": bool(include_global_search),
        "include_groups": bool(include_groups),
        "action_id": action_id,
    }
    if normalized_session:
        payload["session_name"] = normalized_session

    repo = TaskQueueRepo()
    result = await repo.enqueue(
        EnqueueInput(
            task_type_code=TELEGRAM_DISCOVER,
            payload=payload,
            dedup_key=_telegram_discover_dedup_key(
                session_stripped or None,
                trimmed_query,
                first_pass_limit=first_pass_limit,
                similarity_depth=similarity_depth,
                include_global_search=include_global_search,
                include_groups=include_groups,
            ),
            created_by=CREATED_BY_DISCOVER,
            account_id=account_id,
        )
    )
    task_id = _task_id_from_enqueue(result)
    return EnqueueTelegramDiscoverResult(task_id=task_id, action_id=action_id)
