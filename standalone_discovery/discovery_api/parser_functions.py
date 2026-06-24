import asyncio
import logging
import os
import time
from typing import Any, Optional, Tuple

import aiohttp
import dotenv
import telethon
from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl import functions as tl_functions
from telethon.tl import types as tl_types

from discovery_api.config import (
    get_dispatch_workers,
    get_entity_resolve_delay_seconds,
    get_http_per_host_limit,
    get_http_pool_limit,
    get_http_timeout_seconds,
    get_queue_maxsize,
    get_webhook_api_key,
)
from discovery_api.chat_resolve import (
    ChannelHasNoDiscussionError,
    ChatAccessError,
    normalize_chat_ref,
    resolve_listen_target,
)
from discovery_api.entity_cache import get_cached_chat_ids, set_cached_chat_id

dotenv.load_dotenv()

log = logging.getLogger(__name__)

_message_queue: asyncio.Queue[dict[str, Any]] | None = None
_webhook_senders: dict[str, "AsyncSender"] = {}
_worker_tasks: list[asyncio.Task[None]] = []

_stats: dict[str, int] = {"enqueued": 0, "dropped": 0, "delivered": 0, "webhook_errors": 0}

# In-memory кеш публичной информации об отправителях. Ключ — sender_id (int),
# значение — (timestamp последнего обновления, словарь sender-полей).
# TTL и включение GetFullUserRequest регулируются env-переменными
# `PARSER_SENDER_CACHE_TTL` и `PARSER_RESOLVE_FULL_USER`.
_sender_cache: dict[int, tuple[float, dict[str, Any]]] = {}


def _get_sender_cache_ttl_seconds() -> float:
    raw = os.getenv("PARSER_SENDER_CACHE_TTL", "300")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 300.0


def _is_full_user_resolve_enabled() -> bool:
    return os.getenv("PARSER_RESOLVE_FULL_USER", "1").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def reset_sender_cache() -> None:
    """Сбрасывает in-memory кеш sender-info. Используется в тестах."""
    _sender_cache.clear()


class AsyncSender:
    def __init__(self, webhook_url: str, *, api_key: str | None = None):
        self.webhook_url = webhook_url
        self.api_key = (api_key or "").strip() or None
        connector = aiohttp.TCPConnector(
            limit=get_http_pool_limit(),
            limit_per_host=get_http_per_host_limit(),
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
        )
        timeout = aiohttp.ClientTimeout(total=get_http_timeout_seconds())
        self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)

    async def send_message(self, message: dict[str, Any]) -> Any:
        headers = {"X-API-Key": self.api_key} if self.api_key else None
        try:
            async with self.session.post(
                self.webhook_url,
                json=message,
                headers=headers,
            ) as response:
                ct = response.headers.get("Content-Type", "")
                if "application/json" in ct.lower():
                    try:
                        return await response.json(content_type=None)
                    except aiohttp.ContentTypeError:
                        return await response.text()
                return await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            log.exception("Ошибка HTTP при отправке на webhook %s", self.webhook_url[:96])
            raise

    async def close(self) -> None:
        await self.session.close()


def _ensure_message_queue() -> asyncio.Queue[dict[str, Any]]:
    global _message_queue
    if _message_queue is None:
        _message_queue = asyncio.Queue(maxsize=get_queue_maxsize())
    return _message_queue


def _ensure_sender(webhook_url: str) -> AsyncSender:
    if webhook_url not in _webhook_senders:
        _webhook_senders[webhook_url] = AsyncSender(
            webhook_url, api_key=get_webhook_api_key()
        )
    return _webhook_senders[webhook_url]


async def send_message_to_webhook(message: dict[str, Any]) -> None:
    webhook_url = message.get("webhook_url")
    if not webhook_url:
        log.warning("Сообщение без webhook_url, пропускаем отправку")
        return
    sender = _webhook_senders.get(webhook_url)
    if sender is None:
        log.warning("AsyncSender для %s не найден, создаём", webhook_url[:48])
        sender = _ensure_sender(webhook_url)

    payload = {k: v for k, v in message.items() if k != "webhook_url"}
    await sender.send_message(payload)


async def _dispatch_queue_worker() -> None:
    assert _message_queue is not None
    while True:
        item = await _message_queue.get()
        try:
            await send_message_to_webhook(item)
            _stats["delivered"] += 1
        except Exception:
            _stats["webhook_errors"] += 1
            log.exception("Ошибка при отправке сообщения на webhook")
        finally:
            _message_queue.task_done()


def _ensure_dispatch_workers(n: int | None = None) -> None:
    target = int(n or get_dispatch_workers())
    if target <= 0:
        target = 1

    alive = [t for t in _worker_tasks if not t.done()]
    _worker_tasks[:] = alive
    missing = target - len(_worker_tasks)
    for i in range(missing):
        _worker_tasks.append(
            asyncio.create_task(
                _dispatch_queue_worker(),
                name=f"telegram-webhook-dispatch-{len(_worker_tasks)+i}",
            )
        )


def get_runtime_stats() -> dict[str, int]:
    return dict(_stats)


def get_runtime_queue_size() -> int:
    q = _message_queue
    return int(q.qsize()) if q is not None else 0


def set_parser_params(session_name: str, channel_list: list[str], webhook_url: str) -> dict[str, Any]:
    allowed_chat_ids: set[int] = set()
    pending_refs: list[str] = []

    for raw in channel_list:
        s = (raw or "").strip()
        if not s:
            continue
        pending_refs.append(s)

    return {
        "session_name": session_name,
        "channel_list": list(channel_list),
        "allowed_chat_ids": allowed_chat_ids,
        "pending_refs": pending_refs,
        "pending_usernames": pending_refs,
        "webhook_url": webhook_url,
    }


def _extract_sender_info(sender: Any) -> dict[str, Any]:
    """Достаёт из Telethon-объекта sender (`User` / `Channel` / `Chat`)
    публичную часть: ничего, что требовало бы дополнительных RPC.

    Возвращает словарь с минимум полем `type`. Поля, которых нет у конкретной
    сущности, ставятся в None.
    """
    if sender is None:
        return {"type": "unknown"}

    sender_id_attr = getattr(sender, "id", None)
    try:
        peer_id = int(telethon.utils.get_peer_id(sender)) if sender_id_attr is not None else None
    except Exception:
        peer_id = int(sender_id_attr) if sender_id_attr is not None else None

    base: dict[str, Any] = {
        "id": peer_id,
        "verified": getattr(sender, "verified", None),
        "scam": getattr(sender, "scam", None),
        "fake": getattr(sender, "fake", None),
        "restricted": getattr(sender, "restricted", None),
        "restriction_reason": _serialize_restriction_reason(
            getattr(sender, "restriction_reason", None)
        ),
    }

    if isinstance(sender, tl_types.User):
        base.update(
            {
                "type": "bot" if getattr(sender, "bot", False) else "user",
                "username": getattr(sender, "username", None),
                "first_name": getattr(sender, "first_name", None),
                "last_name": getattr(sender, "last_name", None),
                "phone": getattr(sender, "phone", None),
                "bot": bool(getattr(sender, "bot", False)),
                "premium": bool(getattr(sender, "premium", False)),
                "deleted": bool(getattr(sender, "deleted", False)),
                "lang_code": getattr(sender, "lang_code", None),
                "is_self": bool(getattr(sender, "is_self", False)),
                "contact": bool(getattr(sender, "contact", False)),
                "mutual_contact": bool(getattr(sender, "mutual_contact", False)),
            }
        )
        return base

    if isinstance(sender, tl_types.Channel):
        # Канал или супергруппа: видны те же флаги, что и в discovery.
        base.update(
            {
                "type": "channel",
                "title": getattr(sender, "title", None),
                "username": getattr(sender, "username", None),
                "participants_count": getattr(sender, "participants_count", None),
                "broadcast": bool(getattr(sender, "broadcast", False)),
                "megagroup": bool(getattr(sender, "megagroup", False)),
                "gigagroup": bool(getattr(sender, "gigagroup", False)),
                "forum": bool(getattr(sender, "forum", False)),
            }
        )
        return base

    if isinstance(sender, tl_types.Chat):
        # Классический маленький чат.
        base.update(
            {
                "type": "chat",
                "title": getattr(sender, "title", None),
                "participants_count": getattr(sender, "participants_count", None),
                "deactivated": bool(getattr(sender, "deactivated", False)),
            }
        )
        return base

    base.setdefault("type", type(sender).__name__.lower())
    return base


def _serialize_restriction_reason(value: Any) -> Optional[list[dict[str, Any]]]:
    if not value:
        return None
    items: list[dict[str, Any]] = []
    for r in value:
        items.append(
            {
                "platform": getattr(r, "platform", None),
                "reason": getattr(r, "reason", None),
                "text": getattr(r, "text", None),
            }
        )
    return items or None


async def _enrich_with_full_user(
    client: Optional[telethon.TelegramClient],
    sender: Any,
    info: dict[str, Any],
) -> None:
    """Подмешивает в `info` поля из `users.GetFullUserRequest`
    (`about`, `common_chats_count`). Любые ошибки молча игнорируются —
    парсер не должен из-за этого терять сообщения.
    """
    if client is None or sender is None or not isinstance(sender, tl_types.User):
        return
    if not _is_full_user_resolve_enabled():
        return
    try:
        full = await client(tl_functions.users.GetFullUserRequest(id=sender))
    except FloodWaitError as e:
        log.warning(
            "FloodWait при GetFullUserRequest: %s сек, пропускаем",
            getattr(e, "seconds", "?"),
        )
        return
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.debug("Не удалось получить FullUser для %s: %s", info.get("id"), e)
        return

    full_user = getattr(full, "full_user", None)
    if full_user is None:
        return
    info["about"] = getattr(full_user, "about", None)
    info["common_chats_count"] = getattr(full_user, "common_chats_count", None)


async def resolve_sender_info(
    event: Any,
    client: Optional[telethon.TelegramClient],
    *,
    ttl: Optional[float] = None,
) -> dict[str, Any]:
    """Возвращает словарь с публичной информацией об отправителе сообщения.

    Источник:
    1. `event.get_sender()` — Telethon уже знает access_hash из апдейта.
    2. Опционально `GetFullUserRequest` за `about` — если включён
       `PARSER_RESOLVE_FULL_USER` (по умолчанию да) и есть `client`.

    Результат кешируется в памяти по `sender_id` с TTL
    `PARSER_SENDER_CACHE_TTL` (по умолчанию 300 секунд). Любые ошибки
    резолва возвращаются как минимальный словарь `{id, type: "unknown",
    resolve_error: "..."}` — это не должно блокировать доставку сообщения.
    """
    sender_id = getattr(event, "sender_id", None)
    if sender_id is None:
        return {"id": None, "type": "unknown"}

    sender_id_int = int(sender_id)
    effective_ttl = ttl if ttl is not None else _get_sender_cache_ttl_seconds()
    now = time.monotonic()

    cached = _sender_cache.get(sender_id_int)
    if cached is not None and (now - cached[0]) < effective_ttl:
        return dict(cached[1])

    info: dict[str, Any] = {"id": sender_id_int, "type": "unknown"}
    sender = None
    try:
        get_sender = getattr(event, "get_sender", None)
        if get_sender is not None:
            sender = await get_sender()
    except FloodWaitError as e:
        info["resolve_error"] = f"FloodWait {getattr(e, 'seconds', '?')}s"
    except asyncio.CancelledError:
        raise
    except Exception as e:
        info["resolve_error"] = f"get_sender error: {e!s}"

    if sender is not None:
        try:
            info.update(_extract_sender_info(sender))
        except Exception as e:
            info["resolve_error"] = f"extract error: {e!s}"

        try:
            await _enrich_with_full_user(client, sender, info)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            info.setdefault("resolve_error", f"full_user error: {e!s}")

    _sender_cache[sender_id_int] = (now, dict(info))
    return info


def _telegram_message_payload(
    msg: Any, event: Any, *, sender_info: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": msg.id,
        "text": msg.message or "",
        "raw_text": getattr(msg, "raw_text", None),
        "date": msg.date.isoformat() if getattr(msg, "date", None) else None,
        "sender_id": event.sender_id,
        "chat_id": event.chat_id,
        "is_private": event.is_private,
        "is_group": event.is_group,
        "is_channel": event.is_channel,
    }
    if msg.reply_to:
        data["reply_to_msg_id"] = msg.reply_to.reply_to_msg_id
    if sender_info is not None:
        data["sender"] = sender_info
    return {"telegram_message": data}


def listen_for_events(params: dict[str, Any]) -> Any:
    """Регистрирует handler на `client`. Возвращает `handler` для `remove_event_handler`."""
    client: telethon.TelegramClient = params["client"]
    queue: asyncio.Queue[dict[str, Any]] = params["queue"]
    webhook_url = params["webhook_url"]
    allowed_chat_ids: set[int] = params["allowed_chat_ids"]
    on_event = params.get("on_event")

    handler = _make_new_message_handler(
        client=client,
        allowed_chat_ids=allowed_chat_ids,
        queue=queue,
        webhook_url=webhook_url,
        on_event=on_event,
    )
    client.add_event_handler(handler, events.NewMessage())
    return handler


def _make_new_message_handler(
    *,
    allowed_chat_ids: set[int],
    queue: asyncio.Queue[dict[str, Any]],
    webhook_url: str,
    client: Optional[telethon.TelegramClient] = None,
    on_event: Optional[Any] = None,
):
    async def handle_new_message(event: Any) -> None:
        chat_id = getattr(event, "chat_id", None)
        if chat_id is None or int(chat_id) not in allowed_chat_ids:
            return

        if on_event is not None:
            try:
                on_event()
            except Exception:
                log.debug("on_event callback бросил исключение", exc_info=True)

        sender_info = await resolve_sender_info(event, client)

        envelope = {
            "webhook_url": webhook_url,
            **_telegram_message_payload(
                event.message, event, sender_info=sender_info
            ),
        }
        try:
            queue.put_nowait(envelope)
            _stats["enqueued"] += 1
        except asyncio.QueueFull:
            _stats["dropped"] += 1
            log.warning(
                "Очередь парсера переполнена, сообщение отброшено: chat_id=%s",
                chat_id,
            )

    return handle_new_message


def _normalize_channel_ref(raw: str) -> str:
    """Обёртка над `normalize_chat_ref`; всегда возвращает str для кеша."""
    ref = normalize_chat_ref(raw)
    if ref == "" or ref is None:
        return ""
    return str(ref)


async def resolve_channel_to_chat_id(
    client: telethon.TelegramClient, raw: str
) -> Tuple[Optional[int], Optional[str]]:
    """Превращает `@username` / `t.me/...` / числовой id в `chat_id` (int).

    Возвращает кортеж `(chat_id, error)`. Если `chat_id is None`, в `error`
    лежит человекочитаемая причина (пустое значение, FloodWait, нет такого
    канала и т.п.). По возможности результат resolve кешируется через
    `entity_cache` — повторные вызовы для того же `@username` не дёргают
    Telegram.
    """
    normalized = _normalize_channel_ref(raw)
    if not normalized:
        return None, f"Пустое или некорректное значение: '{raw}'"

    cached = get_cached_chat_ids([normalized])
    if normalized in cached:
        listen_peer_id = int(cached[normalized])
        log.info(
            "parser resolve cache hit ref=%s listen_peer_id=%s (доступ не перепроверялся)",
            raw,
            listen_peer_id,
        )
        return listen_peer_id, None

    if not client.is_connected():
        return None, (
            f"Telethon-клиент ещё не подключён, resolve '{normalized}' невозможен"
        )

    cache_key = _normalize_channel_ref(raw) or normalized
    try:
        log.info("parser resolve_channel_to_chat_id: ref=%s", raw)
        target = await resolve_listen_target(client, raw, join=True)
        listen_peer_id = int(target.listen_peer_id)
        set_cached_chat_id(cache_key, listen_peer_id)
        log.info(
            "parser resolve OK ref=%s listen_peer_id=%s kind=%s mode=%s "
            "source_joined=%s listen_joined=%s access=%s",
            raw,
            listen_peer_id,
            target.entity_kind,
            target.listen_mode,
            target.source_joined,
            target.listen_joined,
            target.has_listen_access,
        )
        return listen_peer_id, None
    except ChannelHasNoDiscussionError as e:
        log.warning("parser resolve skip (no discussion) ref=%s: %s", raw, e)
        return None, str(e)
    except ChatAccessError as e:
        log.warning("parser resolve skip (no access) ref=%s: %s", raw, e)
        return None, str(e)
    except ValueError as e:
        log.warning("parser resolve skip ref=%s: %s", raw, e)
        return None, str(e)
    except FloodWaitError as e:
        seconds = int(getattr(e, "seconds", 1) or 1)
        return None, f"FloodWait {seconds}s при resolve '{normalized}'"
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return None, f"Ошибка resolve '{normalized}': {e!s}"


async def _resolve_pending_usernames(
    client: telethon.TelegramClient,
    pending_usernames: list[str],
    allowed_chat_ids: set[int],
) -> None:
    if not pending_usernames:
        return

    cache_keys = [_normalize_channel_ref(ref) or ref.strip() for ref in pending_usernames]
    cached = get_cached_chat_ids([k for k in cache_keys if k])
    for _, chat_id in cached.items():
        allowed_chat_ids.add(int(chat_id))

    delay = float(get_entity_resolve_delay_seconds())

    for raw_ref in pending_usernames:
        cache_key = _normalize_channel_ref(raw_ref) or raw_ref.strip()
        if cache_key and cache_key in cached:
            continue
        try:
            log.info("parser pending resolve: ref=%s", raw_ref)
            target = await resolve_listen_target(client, raw_ref, join=True)
            allowed_chat_ids.add(int(target.listen_peer_id))
            if cache_key:
                set_cached_chat_id(cache_key, int(target.listen_peer_id))
            log.info(
                "parser pending OK ref=%s listen_peer_id=%s joined_src=%s joined_listen=%s",
                raw_ref,
                target.listen_peer_id,
                target.source_joined,
                target.listen_joined,
            )
        except ChannelHasNoDiscussionError as e:
            log.warning("Пропуск канала без обсуждений %s: %s", raw_ref, e)
        except ChatAccessError as e:
            log.warning("Пропуск: нет доступа %s: %s", raw_ref, e)
        except FloodWaitError as e:
            seconds = int(getattr(e, "seconds", 1) or 1)
            log.warning("FloodWait при resolve %s: %s сек", raw_ref, seconds)
            await asyncio.sleep(seconds)
            continue
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Не удалось resolve ref=%s", raw_ref)
        if delay > 0:
            await asyncio.sleep(delay)


async def run_session_listener(
    *,
    session_name: str,
    webhook_url: str,
    allowed_chat_ids: set[int],
    pending_refs: list[str] | None = None,
    client: Optional[telethon.TelegramClient] = None,
    on_event: Optional[Any] = None,
) -> None:
    """
    Один listener на session_name: handler + resolve pending + run_until_disconnected.
    Клиент не disconnect'ится — управляется session_registry.

    `on_event` — необязательный колбэк (без аргументов), вызывается при каждом
    входящем сообщении (для обновления `last_event_at` в health сессии).
    """
    from discovery_api.session_registry import get_or_create_client

    queue = _ensure_message_queue()
    _ensure_sender(webhook_url)
    _ensure_dispatch_workers()

    tg_client = client or await get_or_create_client(session_name)
    params: dict[str, Any] = {
        "session_name": session_name,
        "webhook_url": webhook_url,
        "allowed_chat_ids": allowed_chat_ids,
        "client": tg_client,
        "queue": queue,
        "on_event": on_event,
    }

    handler = listen_for_events(params)

    refs = list(pending_refs or [])
    resolve_task: asyncio.Task[None] | None = None
    try:
        if refs:
            resolve_task = asyncio.create_task(
                _resolve_pending_usernames(tg_client, refs, allowed_chat_ids),
                name="telegram-entity-resolve",
            )
        await tg_client.run_until_disconnected()
    finally:
        if resolve_task is not None and not resolve_task.done():
            resolve_task.cancel()
            try:
                await resolve_task
            except asyncio.CancelledError:
                pass
        try:
            tg_client.remove_event_handler(handler)
        except Exception:
            log.debug("Не удалось снять handler парсера", exc_info=True)


async def start_parser(params: dict[str, Any]) -> None:
    """
    Legacy-обёртка: собирает аргументы и вызывает run_session_listener.
    """
    from discovery_api.session_registry import get_or_create_client

    session_name = params["session_name"]
    client = params.get("client") or await get_or_create_client(session_name)
    await run_session_listener(
        session_name=session_name,
        webhook_url=params["webhook_url"],
        allowed_chat_ids=params["allowed_chat_ids"],
        pending_refs=list(
            params.get("pending_refs") or params.get("pending_usernames") or []
        ),
        client=client,
    )
