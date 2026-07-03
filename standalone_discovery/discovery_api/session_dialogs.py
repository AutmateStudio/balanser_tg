"""Проверка реального членства Telegram-аккаунта в каналах/группах.

Запускается при регистрации/авторизации аккаунта (QR-логин, `enroll-session`,
`reactivate`), чтобы до назначения новых каналов узнать:

- сколько каналов/супергрупп аккаунт уже слушает в Telegram — относительно
  лимита `MAX_CHANNELS_PER_SESSION` (по умолчанию 500);
- сколько из уже требуемых парсеру каналов (общий пул `SessionClump`) на этом
  аккаунте уже есть — чтобы не делать лишний `JoinChannel` (риск флуда/лимита
  на аккаунт, у которого нужный канал и так уже открыт).

Список диалогов читается через `client.iter_dialogs()` — прямой RPC к
Telegram, не кэш. Любая сетевая ошибка гасится и возвращается в поле `error`
снапшота, чтобы диагностика никогда не ломала enroll/QR/reactivate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from telethon import TelegramClient

from discovery_api.session_registry import get_or_create_client

if TYPE_CHECKING:
    from discovery_api.session_registry import SessionClump

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountMembershipSnapshot:
    """Результат сверки реального Telegram-членства аккаунта с требуемыми каналами."""

    telegram_channel_count: int
    required_channel_total: int
    required_channel_present: int
    error: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "telegram_channel_count": self.telegram_channel_count,
            "required_channel_total": self.required_channel_total,
            "required_channel_present": self.required_channel_present,
            "membership_check_error": self.error,
        }


_EMPTY = AccountMembershipSnapshot(0, 0, 0)


def _required_chat_ids(clump: "SessionClump") -> set[int]:
    """Chat_id уже требуемых парсеру каналов — union `ref_to_chat_id` всех сессий clump'а.

    Это уже резолвленные (реально добавленные хоть на одну сессию) каналы пула,
    а не сырые ссылки/юзернеймы — их можно напрямую сравнивать с id из диалогов.
    """
    out: set[int] = set()
    for pc in clump.parser_client_list:
        out.update(int(cid) for cid in pc.ref_to_chat_id.values())
    return out


async def _collect_dialog_channel_ids(client: TelegramClient) -> set[int]:
    """Id всех каналов/супергрупп, в которых состоит клиент (без обычных чатов/ЛС)."""
    ids: set[int] = set()
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if (
            getattr(entity, "broadcast", False)
            or getattr(entity, "megagroup", False)
            or getattr(entity, "gigagroup", False)
        ):
            ids.add(int(entity.id))
    return ids


async def scan_client_channel_membership(
    client: TelegramClient,
    clump: Optional["SessionClump"] = None,
    *,
    session_name: str = "",
) -> AccountMembershipSnapshot:
    """Сверяет диалоги уже подключённого `client` с требуемыми каналами `clump`.

    Используется там, где клиент уже есть в руках (например, только что
    залогиненный по QR) — чтобы не открывать второй коннект к тому же `.session`.
    """
    try:
        dialog_ids = await _collect_dialog_channel_ids(client)
    except Exception as exc:  # noqa: BLE001 — диагностика не должна ронять вызывающий код
        log.warning(
            "Membership-check: iter_dialogs упал для %s: %s", session_name or "?", exc
        )
        return AccountMembershipSnapshot(0, 0, 0, error=str(exc))

    required_total = 0
    required_present = 0
    if clump is not None:
        required_ids = _required_chat_ids(clump)
        required_total = len(required_ids)
        required_present = len(required_ids & dialog_ids)

    snapshot = AccountMembershipSnapshot(
        telegram_channel_count=len(dialog_ids),
        required_channel_total=required_total,
        required_channel_present=required_present,
    )
    log.info(
        "Membership-check %s: telegram_channels=%d, требуемых уже на аккаунте=%d/%d",
        session_name or "?",
        snapshot.telegram_channel_count,
        snapshot.required_channel_present,
        snapshot.required_channel_total,
    )
    return snapshot


async def scan_account_channel_membership(
    session_name: str,
    clump: Optional["SessionClump"] = None,
) -> AccountMembershipSnapshot:
    """Резолвит клиент через реестр (по `.session`-файлу) и сверяет членство."""
    try:
        client = await get_or_create_client(session_name)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Membership-check: не удалось получить клиент для %s: %s", session_name, exc
        )
        return AccountMembershipSnapshot(0, 0, 0, error=str(exc))
    return await scan_client_channel_membership(client, clump, session_name=session_name)
