from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from discovery_api.client_sessions.errors import (
    ClientSessionError,
    PasswordRequired,
    SessionRevokedError,
)
from discovery_api.client_sessions.gateway import ClientGateway, HistoryItem, QrHandle
from discovery_api.client_sessions.store import FileSessionStore, resolve_sessions_dir
from discovery_api.client_sessions.telethon_gateway import TelethonGateway
from discovery_api.client_sessions.webhook import WebhookNotifier

log = logging.getLogger(__name__)

FORBIDDEN_RESPONSE_KEYS = {"session_string", "sessionString", "session_file", "password"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key in FORBIDDEN_RESPONSE_KEYS:
            continue
        if isinstance(value, dict):
            clean[key] = public_payload(value)
        elif isinstance(value, list):
            clean[key] = [public_payload(item) if isinstance(item, dict) else item for item in value]
        else:
            clean[key] = value
    return clean


class ClientSessionService:
    def __init__(
        self,
        store: FileSessionStore,
        gateway: ClientGateway,
        webhook: WebhookNotifier | None = None,
        qr_ttl_seconds: float = 300,
        auto_listen: bool = True,
        poll_seconds: float = 8,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.webhook = webhook or WebhookNotifier()
        self.qr_ttl_seconds = qr_ttl_seconds
        self.auto_listen = auto_listen
        self.poll_seconds = poll_seconds
        self._qr: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._listener: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def _lock(self, remote_account_id: str) -> asyncio.Lock:
        lock = self._locks.get(remote_account_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[remote_account_id] = lock
        return lock

    async def open_qr(self, remote_account_id: str) -> dict[str, Any]:
        meta = self.store.ensure(remote_account_id)
        if meta.get("status") == "revoked":
            meta["status"] = "pending"
        handle = await self.gateway.begin_qr(self.store.session_path(remote_account_id))
        qr_session_id = uuid.uuid4().hex
        self._qr[qr_session_id] = {
            "qr_session_id": qr_session_id,
            "remote_account_id": remote_account_id,
            "status": "pending",
            "qr_url": handle.qr_url,
            "handle": handle,
            "telegram_user_id": None,
            "username": None,
            "display_name": None,
            "error": None,
        }
        meta["qr_session_id"] = qr_session_id
        meta["status"] = "pending"
        self.store.save(meta)
        return self.qr_view(qr_session_id)

    async def await_qr(self, qr_session_id: str) -> None:
        state = self._qr.get(qr_session_id)
        if not state or state["status"] != "pending":
            return
        handle: QrHandle = state["handle"]
        try:
            profile = await asyncio.wait_for(handle.wait(), timeout=self.qr_ttl_seconds)
        except PasswordRequired:
            state["status"] = "password_required"
            return
        except asyncio.TimeoutError:
            state["status"] = "expired"
            state["error"] = "Время QR истекло"
            return
        except asyncio.CancelledError:
            state["status"] = "canceled"
            return
        except ClientSessionError as exc:
            state["status"] = "error"
            state["error"] = str(exc)
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("client sessions: QR завершился ошибкой: %s", type(exc).__name__)
            state["status"] = "error"
            state["error"] = "Не удалось подтвердить QR"
            return
        self._activate(state, profile.telegram_user_id, profile.username, profile.display_name)

    async def submit_password(self, qr_session_id: str, password: str) -> dict[str, Any]:
        state = self._qr.get(qr_session_id)
        if not state:
            raise KeyError(qr_session_id)
        if state["status"] != "password_required":
            raise ClientSessionError("qr_state", "Пароль сейчас не запрашивается")
        handle: QrHandle = state["handle"]
        try:
            profile = await handle.submit_password(password)
        except ClientSessionError as exc:
            state["status"] = "error"
            state["error"] = str(exc)
            raise
        self._activate(state, profile.telegram_user_id, profile.username, profile.display_name)
        return self.qr_view(qr_session_id)

    async def cancel_qr(self, qr_session_id: str) -> dict[str, Any]:
        state = self._qr.get(qr_session_id)
        if not state:
            raise KeyError(qr_session_id)
        handle: QrHandle = state["handle"]
        await handle.cancel()
        state["status"] = "canceled"
        return self.qr_view(qr_session_id)

    def qr_view(self, qr_session_id: str) -> dict[str, Any]:
        state = self._qr.get(qr_session_id)
        if not state:
            raise KeyError(qr_session_id)
        return public_payload(
            {
                "qr_session_id": state["qr_session_id"],
                "remote_account_id": state["remote_account_id"],
                "status": state["status"],
                "qr_url": state.get("qr_url"),
                "telegram_user_id": state.get("telegram_user_id"),
                "username": state.get("username"),
                "display_name": state.get("display_name"),
                "error": state.get("error"),
            }
        )

    def _activate(
        self,
        state: dict[str, Any],
        telegram_user_id: int,
        username: str | None,
        display_name: str | None,
    ) -> None:
        state["status"] = "success"
        state["telegram_user_id"] = telegram_user_id
        state["username"] = username
        state["display_name"] = display_name
        meta = self.store.ensure(state["remote_account_id"])
        meta["status"] = "active"
        meta["telegram_user_id"] = str(telegram_user_id)
        meta["username"] = username
        meta["display_name"] = display_name
        self.store.save(meta)

    async def revoke(self, remote_account_id: str) -> dict[str, Any]:
        meta = self.store.get(remote_account_id)
        if not meta:
            raise KeyError(remote_account_id)
        async with self._lock(remote_account_id):
            await self.gateway.revoke(self.store.session_path(remote_account_id))
            meta["status"] = "revoked"
            meta["watched_peers"] = []
            self.store.save(meta)
        return public_payload({"remote_account_id": remote_account_id, "status": "revoked"})

    async def send_dm(
        self,
        remote_account_id: str,
        peer_id: str | None,
        username: str | None,
        text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        cached = self.store.idempotency(remote_account_id, idempotency_key)
        if cached:
            return public_payload(cached)
        meta = self._require_active(remote_account_id)
        async with self._lock(remote_account_id):
            cached = self.store.idempotency(remote_account_id, idempotency_key)
            if cached:
                return public_payload(cached)
            try:
                message_id = await self.gateway.send_dm(
                    self.store.session_path(remote_account_id),
                    peer_id,
                    username,
                    text,
                )
            except ClientSessionError as exc:
                if isinstance(exc, SessionRevokedError):
                    meta["status"] = "revoked"
                    self.store.save(meta)
                payload = {
                    "status": "failed",
                    "telegram_message_id": None,
                    "error_code": exc.code,
                    "error_text": str(exc),
                    "retry_after_seconds": exc.retry_after_seconds,
                }
                self.store.remember_idempotency(remote_account_id, idempotency_key, payload)
                return public_payload(payload)
            payload = {
                "status": "sent",
                "telegram_message_id": str(message_id),
                "error_code": None,
                "error_text": None,
                "retry_after_seconds": None,
            }
            self.store.remember_idempotency(remote_account_id, idempotency_key, payload)
        peer = peer_id or (f"username:{username}" if username else None)
        if peer:
            await self.watch(remote_account_id, peer, username)
            self._advance_cursor(remote_account_id, peer, str(message_id))
        return public_payload(payload)

    async def history(
        self,
        remote_account_id: str,
        peer_id: str | None,
        username: str | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        self._require_active(remote_account_id)
        async with self._lock(remote_account_id):
            try:
                items, next_cursor = await self.gateway.get_history(
                    self.store.session_path(remote_account_id),
                    peer_id,
                    username,
                    cursor,
                    limit,
                    False,
                )
            except ClientSessionError as exc:
                return public_payload(
                    {
                        "messages": [],
                        "next_cursor": None,
                        "error_code": exc.code,
                        "error_text": str(exc),
                    }
                )
        peer = peer_id or (f"username:{username}" if username else None)
        if peer:
            await self.watch(remote_account_id, peer, username)
        return public_payload(
            {
                "messages": [_message_view(item) for item in items],
                "next_cursor": next_cursor,
                "error_code": None,
                "error_text": None,
            }
        )

    async def watch(self, remote_account_id: str, peer_id: str, username: str | None = None) -> None:
        meta = self._require_active(remote_account_id)
        peers = list(meta.get("watched_peers") or [])
        if peer_id not in peers:
            peers.append(peer_id)
            meta["watched_peers"] = peers
        cursors = dict(meta.get("cursors") or {})
        if peer_id not in cursors:
            cursors[peer_id] = await self._latest_cursor(remote_account_id, peer_id, username)
            meta["cursors"] = cursors
        self.store.save(meta)
        if self.auto_listen:
            self.ensure_listener()

    async def poll_once(self) -> list[dict[str, Any]]:
        delivered: list[dict[str, Any]] = []
        for meta in self.store.list_watched():
            remote_account_id = str(meta["remote_account_id"])
            if meta.get("status") != "active":
                continue
            async with self._lock(remote_account_id):
                fresh = self.store.get(remote_account_id) or meta
                cursors = dict(fresh.get("cursors") or {})
                for peer in list(fresh.get("watched_peers") or []):
                    username = peer.removeprefix("username:") if peer.startswith("username:") else None
                    peer_id = None if username else peer
                    cursor = str(cursors.get(peer) or "0")
                    try:
                        items, _next = await self.gateway.get_history(
                            self.store.session_path(remote_account_id),
                            peer_id,
                            username,
                            cursor,
                            20,
                            True,
                        )
                    except ClientSessionError as exc:
                        log.info(
                            "client sessions: история %s/%s: %s",
                            remote_account_id,
                            peer,
                            exc.code,
                        )
                        continue
                    last_ok = cursor
                    for item in items:
                        if item.telegram_message_id <= int(last_ok or "0"):
                            continue
                        if item.outgoing:
                            last_ok = str(item.telegram_message_id)
                            continue
                        payload = {
                            "remote_account_id": remote_account_id,
                            "peer_id": peer,
                            "telegram_message_id": str(item.telegram_message_id),
                            "text": item.text,
                            "occurred_at": _iso(item.occurred_at),
                            "direction": "incoming",
                        }
                        status = await self.webhook.send(payload)
                        if status >= 500 or status == 401:
                            break
                        last_ok = str(item.telegram_message_id)
                        delivered.append(payload)
                    cursors[peer] = last_ok
                fresh["cursors"] = cursors
                self.store.save(fresh)
        return delivered

    def ensure_listener(self) -> None:
        if self._listener and not self._listener.done():
            return
        self._stop.clear()
        self._listener = asyncio.create_task(self._loop())

    async def resume(self) -> None:
        if self.store.list_watched():
            self.ensure_listener()

    async def stop(self) -> None:
        self._stop.set()
        if self._listener:
            self._listener.cancel()
            try:
                await self._listener
            except asyncio.CancelledError:
                pass
            self._listener = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001
                log.warning("client sessions: цикл listener упал", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                continue

    async def _latest_cursor(
        self,
        remote_account_id: str,
        peer_id: str,
        username: str | None,
    ) -> str:
        numeric = None if peer_id.startswith("username:") else peer_id
        name = username or (peer_id.removeprefix("username:") if peer_id.startswith("username:") else None)
        try:
            items, _next = await self.gateway.get_history(
                self.store.session_path(remote_account_id),
                numeric,
                name,
                None,
                1,
                False,
            )
        except ClientSessionError:
            return "0"
        if not items:
            return "0"
        return str(max(item.telegram_message_id for item in items))

    def _advance_cursor(self, remote_account_id: str, peer: str, message_id: str) -> None:
        meta = self.store.get(remote_account_id)
        if not meta:
            return
        cursors = dict(meta.get("cursors") or {})
        current = int(cursors.get(peer) or "0")
        if int(message_id) > current:
            cursors[peer] = message_id
            meta["cursors"] = cursors
            self.store.save(meta)

    def _require_active(self, remote_account_id: str) -> dict[str, Any]:
        meta = self.store.get(remote_account_id)
        if not meta or meta.get("status") != "active":
            raise SessionRevokedError()
        return meta


def _message_view(item: HistoryItem) -> dict[str, Any]:
    return {
        "telegram_message_id": str(item.telegram_message_id),
        "text": item.text,
        "occurred_at": _iso(item.occurred_at),
        "outgoing": item.outgoing,
    }


_service: ClientSessionService | None = None


def build_default_service() -> ClientSessionService:
    root = resolve_sessions_dir()
    if root is None:
        raise RuntimeError("CLIENT_SESSIONS_DIR не задан")
    poll = float(os.getenv("CLIENT_LISTENER_POLL_SECONDS", "8"))
    ttl = float(os.getenv("CLIENT_QR_TTL_SECONDS", "300"))
    return ClientSessionService(
        FileSessionStore(root),
        TelethonGateway(),
        WebhookNotifier(),
        qr_ttl_seconds=ttl,
        poll_seconds=poll,
    )


def get_service() -> ClientSessionService:
    global _service
    if _service is None:
        _service = build_default_service()
    return _service


def set_service(service: ClientSessionService | None) -> None:
    global _service
    _service = service


async def resume_client_listeners() -> None:
    if resolve_sessions_dir() is None and _service is None:
        log.info("client sessions: CLIENT_SESSIONS_DIR не задан, listener не запущен")
        return
    try:
        await get_service().resume()
    except Exception:  # noqa: BLE001
        log.warning("client sessions: не удалось восстановить listener", exc_info=True)


async def stop_client_listeners() -> None:
    if _service is not None:
        await _service.stop()
