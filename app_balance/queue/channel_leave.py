"""Реальный выход аккаунта из канала и связанной группы обсуждения.

Локальное снятие listener (``remove_channel``) этот модуль не делает:
сначала Telegram LeaveChannel, и только потом вызывающий чистит clump.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LeaveOutcome:
    ok: bool
    left_count: int
    soft_ok: bool


def is_flood_wait(exc: BaseException) -> bool:
    return type(exc).__name__ in {"FloodWaitError", "FloodPremiumWaitError"}


def is_soft_leave_error(exc: BaseException) -> bool:
    """Канал уже покинут или недоступен — выход считать успешным."""
    name = type(exc).__name__
    if name in {
        "UserNotParticipantError",
        "ChannelPrivateError",
        "ChannelInvalidError",
        "UsernameNotOccupiedError",
        "UsernameInvalidError",
        "ChatIdInvalidError",
        "PeerIdInvalidError",
    }:
        return True
    msg = str(exc).lower()
    markers = (
        "not a member",
        "not a participant",
        "channel specified is private",
        "no user has",
        "username not occupied",
        "cannot find any entity",
        "chat not found",
    )
    return any(marker in msg for marker in markers)


async def leave_tracked_channel(client: object, ref: str) -> LeaveOutcome:
    """LeaveChannel канала и linked discussion. Без join.

    FloodWait и прочие жёсткие ошибки пробрасываются вызывающему.
    «Уже не участник» и «канал недоступен» — soft-ok.
    """
    from telethon.tl.functions.channels import (
        GetFullChannelRequest,
        LeaveChannelRequest,
    )

    cleaned = (ref or "").strip()
    if not cleaned:
        raise ValueError("пустой channel ref")

    try:
        entity = await client.get_entity(cleaned)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        if is_flood_wait(exc):
            raise
        if is_soft_leave_error(exc):
            log.info("leave: resolve soft-ok ref=%s (%s)", cleaned, exc)
            return LeaveOutcome(ok=True, left_count=0, soft_ok=True)
        raise

    peers: list[object] = [entity]
    try:
        full = await client(GetFullChannelRequest(channel=entity))  # type: ignore[operator]
        linked_id = getattr(getattr(full, "full_chat", None), "linked_chat_id", None)
        if linked_id:
            try:
                linked = await client.get_entity(int(linked_id))  # type: ignore[attr-defined]
                peers.append(linked)
            except Exception as exc:  # noqa: BLE001
                if is_flood_wait(exc) or not is_soft_leave_error(exc):
                    raise
                log.info(
                    "leave: linked discussion недоступна ref=%s (%s)",
                    cleaned,
                    exc,
                )
    except Exception as exc:  # noqa: BLE001
        if is_flood_wait(exc):
            raise
        log.info("leave: GetFullChannel пропущен ref=%s (%s)", cleaned, exc)

    left = 0
    soft = False
    for peer in peers:
        try:
            await client(LeaveChannelRequest(channel=peer))  # type: ignore[operator]
            left += 1
        except Exception as exc:  # noqa: BLE001
            if is_flood_wait(exc):
                raise
            if is_soft_leave_error(exc):
                soft = True
                log.info("leave: soft-ok ref=%s (%s)", cleaned, exc)
                continue
            raise
    return LeaveOutcome(ok=True, left_count=left, soft_ok=soft or left == 0)
