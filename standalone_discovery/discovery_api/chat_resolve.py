"""Определение цели прослушивания: канал → чат обсуждений, группа → сам чат."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Union

import telethon
from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    InviteHashExpiredError,
    InviteRequestSentError,
    UserAlreadyParticipantError,
    UserNotParticipantError,
)
from telethon.tl import functions, types

log = logging.getLogger(__name__)

EntityKind = Literal["channel", "supergroup", "group", "user", "unknown"]
ListenMode = Literal["discussion", "group_chat"]


class ChannelHasNoDiscussionError(ValueError):
    """Broadcast-канал без привязанного чата обсуждений."""


class ChatAccessError(ValueError):
    """Нет доступа к чату, который нужно слушать (не участник / приватный)."""


@dataclass(frozen=True)
class ListenTarget:
    source_entity: Any
    listen_entity: Any
    source_peer_id: int
    listen_peer_id: int
    entity_kind: EntityKind
    listen_mode: ListenMode
    linked_chat_id: int | None
    title: str
    username: str | None
    full_info: Any | None = None
    source_joined: bool = False
    listen_joined: bool = False
    has_listen_access: bool = False
    access_note: str = ""


def normalize_chat_ref(raw: str) -> Union[str, int]:
    """
    Нормализует ссылку/username в ref для get_entity.

    Возвращает username (str), invite (str) или chat_id (int) для t.me/c/...
    """
    value = (raw or "").strip()
    if not value:
        return value

    for sep in ("?", "#"):
        if sep in value:
            value = value.split(sep, 1)[0]

    if "t.me/" in value:
        value = value.split("t.me/", 1)[-1]
    value = value.strip("/")

    m = re.match(r"^c/(\d+)(?:/.*)?$", value)
    if m:
        return int("-100" + m.group(1))

    if value.startswith("joinchat/") or value.startswith("+"):
        return value

    if "/" in value:
        value = value.split("/", 1)[0]

    if value.startswith("@"):
        value = value[1:]
    return value


def classify_chat_entity(entity: Any) -> EntityKind:
    if isinstance(entity, types.User):
        return "user"
    if isinstance(entity, types.Chat):
        return "group"
    if isinstance(entity, types.Channel):
        if getattr(entity, "broadcast", False):
            return "channel"
        if getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False):
            return "supergroup"
    return "unknown"


def _entity_label(entity: Any) -> str:
    title = getattr(entity, "title", None) or ""
    username = getattr(entity, "username", None)
    if username:
        return f"{title} (@{username})".strip()
    return title or type(entity).__name__


async def _join_channel_entity(
    client: TelegramClient,
    entity: Any,
    *,
    raw_ref: str,
    role: str,
) -> bool:
    """JoinChannelRequest для Channel. Возвращает True, если вступление успешно или уже внутри."""
    if not isinstance(entity, types.Channel):
        log.info(
            "resolve ref=%s: join пропущен для role=%s (тип %s)",
            raw_ref,
            role,
            type(entity).__name__,
        )
        return False

    title = _entity_label(entity)
    try:
        await client(functions.channels.JoinChannelRequest(channel=entity))
        log.info("resolve ref=%s: JoinChannel OK role=%s chat=%s", raw_ref, role, title)
        return True
    except UserAlreadyParticipantError:
        log.info(
            "resolve ref=%s: уже участник role=%s chat=%s",
            raw_ref,
            role,
            title,
        )
        return True
    except InviteRequestSentError as e:
        log.warning(
            "resolve ref=%s: join_pending role=%s chat=%s (заявка отправлена): %s",
            raw_ref,
            role,
            title,
            e,
        )
        return False
    except ChannelPrivateError as e:
        log.warning(
            "resolve ref=%s: приватный чат role=%s chat=%s: %s",
            raw_ref,
            role,
            title,
            e,
        )
        return False
    except InviteHashExpiredError as e:
        log.warning("resolve ref=%s: invite истёк role=%s: %s", raw_ref, role, e)
        return False
    except Exception:
        log.exception(
            "resolve ref=%s: JoinChannel FAIL role=%s chat=%s",
            raw_ref,
            role,
            title,
        )
        return False


async def _check_listen_access(
    client: TelegramClient,
    entity: Any,
    *,
    raw_ref: str,
    role: str,
) -> tuple[bool, str]:
    """Проверяет, что сессия видит чат как участника (может получать сообщения)."""
    label = _entity_label(entity)

    if isinstance(entity, types.Channel):
        try:
            await client(
                functions.channels.GetParticipantRequest(
                    channel=entity,
                    participant=types.InputPeerSelf(),
                )
            )
            log.info(
                "resolve ref=%s: доступ OK role=%s chat=%s (GetParticipant)",
                raw_ref,
                role,
                label,
            )
            return True, "участник"
        except UserNotParticipantError:
            log.warning(
                "resolve ref=%s: нет доступа role=%s chat=%s — не участник",
                raw_ref,
                role,
                label,
            )
            return False, "не участник"
        except ChannelPrivateError as e:
            log.warning(
                "resolve ref=%s: нет доступа role=%s chat=%s — приватный: %s",
                raw_ref,
                role,
                label,
                e,
            )
            return False, "приватный канал/группа"
        except Exception as e:
            log.warning(
                "resolve ref=%s: проверка доступа role=%s chat=%s: %s",
                raw_ref,
                role,
                label,
                e,
            )
            return False, str(e)

    if isinstance(entity, types.Chat):
        try:
            await client.get_permissions(entity)
            log.info(
                "resolve ref=%s: доступ OK role=%s chat=%s (get_permissions)",
                raw_ref,
                role,
                label,
            )
            return True, "участник"
        except Exception as e:
            log.warning(
                "resolve ref=%s: нет доступа role=%s chat=%s: %s",
                raw_ref,
                role,
                label,
                e,
            )
            return False, str(e)

    return False, f"неподдерживаемый тип {type(entity).__name__}"


async def _ensure_membership(
    client: TelegramClient,
    entity: Any,
    *,
    raw_ref: str,
    role: str,
    join: bool,
) -> tuple[bool, bool, str]:
    """
    join (если разрешено) + проверка доступа.
    Возвращает (joined, has_access, note).
    """
    joined = False
    if join and isinstance(entity, types.Channel):
        joined = await _join_channel_entity(client, entity, raw_ref=raw_ref, role=role)
    elif join and isinstance(entity, types.Chat):
        log.info(
            "resolve ref=%s: legacy-группа role=%s — JoinChannel недоступен, нужен invite",
            raw_ref,
            role,
        )

    has_access, note = await _check_listen_access(
        client, entity, raw_ref=raw_ref, role=role
    )
    if join and isinstance(entity, types.Channel) and not joined and not has_access:
        note = f"не удалось вступить; {note}"
    return joined, has_access, note


async def resolve_listen_target(
    client: TelegramClient,
    raw_ref: str,
    *,
    join: bool = True,
    require_listen_access: bool = True,
) -> ListenTarget:
    """
    Резолвит ссылку/username и возвращает сущность для прослушивания.

    - broadcast-канал → linked discussion chat (ошибка, если обсуждений нет)
    - supergroup / group → сам чат
    """
    log.info("resolve start ref=%s join=%s", raw_ref, join)
    ref = normalize_chat_ref(raw_ref)
    if ref == "" or ref is None:
        raise ValueError(f"Пустое или некорректное значение: '{raw_ref}'")

    if isinstance(ref, str) and (ref.startswith("+") or ref.startswith("joinchat/")):
        raise ValueError(
            f"Invite-ссылки пока не поддерживаются (нужен ImportChatInvite): '{raw_ref}'"
        )

    entity = await client.get_entity(ref)
    kind = classify_chat_entity(entity)
    log.info(
        "resolve ref=%s: сущность kind=%s title=%s",
        raw_ref,
        kind,
        _entity_label(entity),
    )

    if kind == "user":
        raise ValueError(f"Ссылка ведёт на пользователя, а не на чат: '{raw_ref}'")
    if kind == "unknown":
        raise ValueError(f"Не удалось определить тип чата: '{raw_ref}'")

    source_joined = False
    if join and kind in ("channel", "supergroup"):
        source_joined = await _join_channel_entity(
            client, entity, raw_ref=raw_ref, role="source"
        )

    listen_entity = entity
    listen_mode: ListenMode = "group_chat"
    linked_chat_id: int | None = None
    full_info = None
    listen_joined = False
    access_note = ""

    if kind == "channel" and isinstance(entity, types.Channel):
        full_info = await client(functions.channels.GetFullChannelRequest(channel=entity))
        full_chat = getattr(full_info, "full_chat", None)
        linked_chat_id = getattr(full_chat, "linked_chat_id", None) if full_chat else None
        if not linked_chat_id:
            title = getattr(entity, "title", None) or str(raw_ref)
            raise ChannelHasNoDiscussionError(
                f"У канала «{title}» нет чата обсуждений — прослушивание невозможно"
            )
        listen_entity = await client.get_entity(linked_chat_id)
        listen_mode = "discussion"
        log.info(
            "resolve ref=%s: чат обсуждений %s (linked_chat_id=%s)",
            raw_ref,
            _entity_label(listen_entity),
            linked_chat_id,
        )
        if join and isinstance(listen_entity, types.Channel):
            listen_joined = await _join_channel_entity(
                client, listen_entity, raw_ref=raw_ref, role="discussion"
            )
    elif join and kind == "supergroup":
        listen_joined = source_joined

    _, has_access, access_note = await _ensure_membership(
        client,
        listen_entity,
        raw_ref=raw_ref,
        role="listen",
        join=False,
    )

    source_peer_id = int(telethon.utils.get_peer_id(entity))
    listen_peer_id = int(telethon.utils.get_peer_id(listen_entity))

    log.info(
        "resolve done ref=%s kind=%s mode=%s source_peer=%s listen_peer=%s "
        "source_joined=%s listen_joined=%s has_access=%s note=%s",
        raw_ref,
        kind,
        listen_mode,
        source_peer_id,
        listen_peer_id,
        source_joined,
        listen_joined,
        has_access,
        access_note,
    )

    if require_listen_access and not has_access:
        raise ChatAccessError(
            f"Нет доступа к чату для прослушивания «{_entity_label(listen_entity)}» "
            f"(ref={raw_ref}, listen_peer_id={listen_peer_id}): {access_note}"
        )

    return ListenTarget(
        source_entity=entity,
        listen_entity=listen_entity,
        source_peer_id=source_peer_id,
        listen_peer_id=listen_peer_id,
        entity_kind=kind,
        listen_mode=listen_mode,
        linked_chat_id=linked_chat_id,
        title=getattr(entity, "title", "") or "",
        username=getattr(entity, "username", None),
        full_info=full_info,
        source_joined=source_joined,
        listen_joined=listen_joined,
        has_listen_access=has_access,
        access_note=access_note,
    )
