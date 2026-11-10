from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from weakref import WeakKeyDictionary

from telethon import errors, types, utils

from discovery_api.client_sessions.errors import (
    FloodWaitError,
    PasswordRequired,
    PeerUnavailableError,
    PrivacyRestrictedError,
    SessionBusyError,
    SessionRevokedError,
)
from discovery_api.client_sessions.gateway import AuthProfile, HistoryItem, SourceMessageContext

log = logging.getLogger(__name__)


def _profile(user: object) -> AuthProfile:
    user_id = int(getattr(user, "id"))
    username = getattr(user, "username", None)
    first = getattr(user, "first_name", None) or ""
    last = getattr(user, "last_name", None) or ""
    display = " ".join(part for part in (first, last) if part).strip() or None
    return AuthProfile(user_id, username, display)


class TelethonQrHandle:
    def __init__(self, client: object, qr_login: object, qr_url: str) -> None:
        self._client = client
        self._qr_login = qr_login
        self.qr_url = qr_url
        self._cancelled = False

    async def wait(self) -> AuthProfile:
        if self._cancelled:
            raise asyncio.CancelledError()
        try:
            user = await self._qr_login.wait()
        except Exception as exc:  # noqa: BLE001
            _reraise_auth(exc)
            raise
        return _profile(user)

    async def submit_password(self, password: str) -> AuthProfile:
        if self._cancelled:
            raise asyncio.CancelledError()
        try:
            await self._client.sign_in(password=password)
            me = await self._client.get_me()
        except Exception as exc:  # noqa: BLE001
            _reraise_auth(exc)
            raise
        return _profile(me)

    async def cancel(self) -> None:
        self._cancelled = True
        disconnect = getattr(self._client, "disconnect", None)
        if disconnect:
            await disconnect()


class TelethonGateway:
    """Файловая сессия Telethon. Session string наружу не отдаётся."""

    def __init__(self) -> None:
        self._clients: dict[str, object] = {}
        self._client_locks: dict[str, asyncio.Lock] = {}
        self._dialog_refreshes: WeakKeyDictionary[object, float] = WeakKeyDictionary()

    async def begin_qr(self, session_path: Path) -> TelethonQrHandle:
        client = await self._client(session_path, fresh=True)
        qr_login = await client.qr_login()
        return TelethonQrHandle(client, qr_login, qr_login.url)

    async def send_dm(
        self,
        session_path: Path,
        peer_id: str | None,
        username: str | None,
        text: str,
        source: SourceMessageContext | None = None,
    ) -> int:
        try:
            client = await self._client(session_path)
            entity = await self._resolve(client, peer_id, username, source)
            message = await client.send_message(entity, text)
        except Exception as exc:  # noqa: BLE001
            _reraise_send(exc)
            raise
        return int(message.id)

    async def get_history(
        self,
        session_path: Path,
        peer_id: str | None,
        username: str | None,
        cursor: str | None,
        limit: int,
        newer: bool,
        source: SourceMessageContext | None = None,
    ) -> tuple[list[HistoryItem], str | None]:
        try:
            client = await self._client(session_path)
            entity = await self._resolve(client, peer_id, username, source)
            kwargs: dict[str, object] = {"limit": limit}
            if cursor and cursor.isdigit() and cursor != "0":
                if newer:
                    kwargs["min_id"] = int(cursor)
                else:
                    kwargs["max_id"] = int(cursor)
            messages = await client.get_messages(entity, **kwargs)
        except Exception as exc:  # noqa: BLE001
            _reraise_send(exc)
            raise
        items = [_history_item(message) for message in messages if getattr(message, "id", None)]
        items.sort(key=lambda item: item.telegram_message_id)
        next_cursor = None
        if not newer and len(items) >= limit:
            next_cursor = str(items[0].telegram_message_id)
        return items, next_cursor

    async def revoke(self, session_path: Path) -> None:
        key = str(session_path)
        async with self._client_lock(session_path):
            client = self._clients.pop(key, None)
            await self._disconnect(client)
        for suffix in (".session", ".session-journal"):
            path = Path(str(session_path) + suffix)
            if path.is_file():
                path.unlink()

    async def get_author_username(
        self,
        session_path: Path,
        peer_id: str,
        source: SourceMessageContext | None,
    ) -> str | None:
        client = await self._client(session_path)
        entity = (
            await self._resolve(client, peer_id, None, source)
            if source else types.PeerUser(int(peer_id))
        )
        profile = await client.get_entity(entity)
        if isinstance(profile, types.User) and profile.id == int(peer_id):
            return profile.username
        return None

    async def _client(self, session_path: Path, fresh: bool = False) -> object:
        from telethon import TelegramClient

        from discovery_api.config import get_api_hash, get_api_id

        key = str(session_path)
        async with self._client_lock(session_path):
            current = self._clients.get(key)
            if current is not None and not fresh:
                if getattr(current, "is_connected", lambda: False)():
                    return current
                try:
                    await current.connect()
                    return current
                except Exception as exc:  # noqa: BLE001
                    _reraise_connection(exc)
                    raise

            if current is not None:
                await self._disconnect(current)
                self._clients.pop(key, None)

            client = TelegramClient(key, get_api_id(), get_api_hash())
            try:
                await client.connect()
            except Exception as exc:  # noqa: BLE001
                await self._disconnect(client)
                _reraise_connection(exc)
                raise
            self._clients[key] = client
            return client

    def _client_lock(self, session_path: Path) -> asyncio.Lock:
        key = str(session_path)
        lock = self._client_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._client_locks[key] = lock
        return lock

    @staticmethod
    async def _disconnect(client: object | None) -> None:
        if client is None:
            return
        disconnect = getattr(client, "disconnect", None)
        if disconnect:
            await disconnect()

    async def _resolve(
        self,
        client: object,
        peer_id: str | None,
        username: str | None,
        source: SourceMessageContext | None,
    ) -> object:
        if username:
            try:
                return await client.get_entity(username)
            except Exception as exc:  # noqa: BLE001
                log.info("client sessions: username не резолвится: %s", type(exc).__name__)
                raise PeerUnavailableError() from exc
        if peer_id and peer_id.isdigit():
            if source is not None:
                user = types.PeerUser(int(peer_id))
                try:
                    target = await client.get_input_entity(user)
                except (ValueError, errors.PeerIdInvalidError, errors.UserIdInvalidError):
                    target = None
                if isinstance(target, types.InputPeerSelf) or (
                    isinstance(target, types.InputPeerUser) and target.user_id == user.user_id
                ):
                    return target
                chat = await self._source_chat(client, source.chat_id, source.chat_username)
                return types.InputPeerUserFromMessage(chat, source.message_id, user.user_id)
            try:
                return await client.get_entity(int(peer_id))
            except Exception as exc:  # noqa: BLE001
                log.info("client sessions: peer id не резолвится: %s", type(exc).__name__)
                raise PeerUnavailableError() from exc
        raise PeerUnavailableError()

    async def _source_chat(self, client: object, chat_id: int, username: str | None) -> object:
        # Старые записи могут хранить ID супергруппы без префикса -100.
        chat = types.PeerChannel(chat_id) if chat_id > 0 else utils.get_peer(chat_id)
        marked_id = utils.get_peer_id(chat)
        try:
            target = await client.get_input_entity(chat)
            if utils.get_peer_id(target) == marked_id:
                return target
        except (ValueError, errors.ChannelInvalidError, errors.ChannelPrivateError):
            pass

        if username:
            try:
                target = await client.get_input_entity(username)
                if utils.get_peer_id(target) == marked_id:
                    return target
            except (ValueError, errors.UsernameInvalidError, errors.UsernameNotOccupiedError):
                pass

        # Новая сессия может ещё не знать группу. Обновляем список доступных
        # диалогов ограниченно, чтобы недоступный источник не держал очередь.
        now = time.monotonic()
        last_refresh = self._dialog_refreshes.get(client)
        if last_refresh is None or now - last_refresh >= 300:
            self._dialog_refreshes[client] = now
            try:
                dialogs = await asyncio.wait_for(client.get_dialogs(), timeout=5)
            except asyncio.TimeoutError:
                raise PeerUnavailableError() from None
            for dialog in dialogs:
                if dialog.id == marked_id:
                    return dialog.input_entity
        raise PeerUnavailableError()


def _history_item(message: object) -> HistoryItem:
    occurred = getattr(message, "date", None) or datetime.now(timezone.utc)
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    text = getattr(message, "message", None) or getattr(message, "text", None) or ""
    return HistoryItem(
        telegram_message_id=int(message.id),
        text=str(text),
        occurred_at=occurred,
        outgoing=bool(getattr(message, "out", False)),
    )


def _reraise_auth(exc: BaseException) -> None:
    name = type(exc).__name__
    if name == "SessionPasswordNeededError":
        raise PasswordRequired() from exc
    if name == "AuthKeyDuplicatedError":
        raise SessionRevokedError() from exc
    if name in {"AuthKeyUnregisteredError", "SessionRevokedError", "UserDeactivatedError"}:
        raise SessionRevokedError() from exc
    if isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower():
        raise SessionBusyError() from exc


def _reraise_send(exc: BaseException) -> None:
    name = type(exc).__name__
    if isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower():
        raise SessionBusyError() from exc
    if name == "FloodWaitError":
        seconds = int(getattr(exc, "seconds", 0) or 0)
        raise FloodWaitError(seconds) from exc
    if name in {"UserPrivacyRestrictedError", "ChatWriteForbiddenError"}:
        raise PrivacyRestrictedError() from exc
    if name in {
        "PeerIdInvalidError",
        "UsernameInvalidError",
        "UsernameNotOccupiedError",
        "InputUserDeactivatedError",
        "ChannelInvalidError",
        "ChannelPrivateError",
        "ChatIdInvalidError",
        "MessageIdInvalidError",
        "MsgIdInvalidError",
    }:
        raise PeerUnavailableError() from exc
    if name in {
        "AuthKeyDuplicatedError",
        "AuthKeyUnregisteredError",
        "SessionRevokedError",
        "UserDeactivatedError",
    }:
        raise SessionRevokedError() from exc
    if isinstance(
        exc,
        (FloodWaitError, PrivacyRestrictedError, PeerUnavailableError, SessionBusyError, SessionRevokedError),
    ):
        raise exc


def _reraise_connection(exc: BaseException) -> None:
    _reraise_send(exc)
