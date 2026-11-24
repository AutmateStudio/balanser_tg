from __future__ import annotations

import asyncio
import logging
import math
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from discovery_api.client_sessions.errors import (
    ClientSessionError,
    FloodWaitError,
    PasswordRequired,
    SessionRevokedError,
)
from discovery_api.client_sessions.gateway import ClientGateway, HistoryItem, QrHandle, SourceMessageContext
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
        push_updates: bool = False,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.webhook = webhook or WebhookNotifier()
        self.qr_ttl_seconds = qr_ttl_seconds
        self.auto_listen = auto_listen
        self.poll_seconds = poll_seconds
        self.push_updates = push_updates
        self._qr: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._listener: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._push_tasks: dict[str, asyncio.Task[None]] = {}
        self._push_signals: dict[str, asyncio.Event] = {}

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
        meta.pop("session_error", None)
        self.store.save(meta)

    async def revoke(self, remote_account_id: str) -> dict[str, Any]:
        meta = self.store.get(remote_account_id)
        if not meta:
            raise KeyError(remote_account_id)
        async with self._lock(remote_account_id):
            await self.gateway.revoke(self.store.session_path(remote_account_id))
            meta["status"] = "revoked"
            meta["watched_peers"] = []
            meta.pop("pending_events", None)
            meta.pop("peer_sources", None)
            meta.pop("peer_source_usernames", None)
            self.store.save(meta)
        task = self._push_tasks.pop(remote_account_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._push_signals.pop(remote_account_id, None)
        return public_payload({"remote_account_id": remote_account_id, "status": "revoked"})

    async def send_dm(
        self,
        remote_account_id: str,
        peer_id: str | None,
        username: str | None,
        text: str,
        idempotency_key: str,
        source: SourceMessageContext | None = None,
    ) -> dict[str, Any]:
        cached = self.store.idempotency(remote_account_id, idempotency_key)
        if cached:
            return public_payload(cached)
        peer = peer_id or (f"username:{username}" if username else None)
        if self.push_updates and peer:
            await self.watch(remote_account_id, peer, username, source)
        async with self._lock(remote_account_id):
            meta = self._require_active(remote_account_id)
            cached = self.store.idempotency(remote_account_id, idempotency_key)
            if cached:
                return public_payload(cached)
            try:
                message_id = await self.gateway.send_dm(
                    self.store.session_path(remote_account_id),
                    peer_id,
                    username,
                    text,
                    source,
                )
            except ClientSessionError as exc:
                if isinstance(exc, FloodWaitError):
                    self._pause_history(meta, exc)
                if isinstance(exc, SessionRevokedError):
                    self._mark_session_revoked(meta, exc)
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
        if peer and not self.push_updates:
            await self.watch(remote_account_id, peer, username, source, initial_cursor=str(message_id))
            async with self._lock(remote_account_id):
                self._advance_cursor(remote_account_id, peer, str(message_id))
        return public_payload(payload)

    @staticmethod
    def _remaining_history_wait(meta: dict[str, Any]) -> int:
        return max(0, math.ceil(meta.get("history_retry_at", 0) - time.time()))

    def _pause_history(self, meta: dict[str, Any], error: FloodWaitError) -> None:
        seconds = max(1, error.retry_after_seconds or 60)
        meta["history_retry_at"] = max(meta.get("history_retry_at", 0), time.time() + seconds)
        self.store.save(meta)
        log.info("client sessions: history flood_wait account=%s wait_seconds=%s",
                 meta["remote_account_id"], seconds)

    async def history(
        self,
        remote_account_id: str,
        peer_id: str | None,
        username: str | None,
        cursor: str | None,
        limit: int,
        source: SourceMessageContext | None = None,
        resolve_author_username: bool = False,
    ) -> dict[str, Any]:
        started_at = time.monotonic()
        author: dict[str, str | None] = {}
        session_path = self.store.session_path(remote_account_id)
        async with self._lock(remote_account_id):
            meta = self._require_active(remote_account_id)
            remaining = self._remaining_history_wait(meta)
            try:
                if remaining:
                    raise FloodWaitError(remaining)
                items, next_cursor = await self.gateway.get_history(
                    session_path,
                    peer_id,
                    username,
                    cursor,
                    limit,
                    False,
                    source,
                )
            except ClientSessionError as exc:
                if isinstance(exc, FloodWaitError) and not remaining:
                    self._pause_history(meta, exc)
                if isinstance(exc, SessionRevokedError):
                    self._mark_session_revoked(meta, exc)
                return public_payload(
                    {
                        "messages": [],
                        "next_cursor": None,
                        "error_code": exc.code,
                        "error_text": str(exc),
                        "retry_after_seconds": exc.retry_after_seconds,
                    }
                )
            if resolve_author_username and peer_id and not username and time.monotonic() - started_at < 10:
                try:
                    author["author_username"] = await asyncio.wait_for(
                        self.gateway.get_author_username(session_path, peer_id, source), timeout=2,
                    )
                except FloodWaitError as exc:
                    self._pause_history(meta, exc)
                except SessionRevokedError as exc:
                    self._mark_session_revoked(meta, exc)
                except Exception as exc:
                    log.info("client sessions: профиль автора не обновлён: %s", type(exc).__name__)
        peer = peer_id or (f"username:{username}" if username else None)
        if peer:
            # Первая страница уже содержит последнее сообщение диалога.
            tip = str(max((item.telegram_message_id for item in items), default=0)) if not cursor else None
            await self.watch(remote_account_id, peer, username, source, initial_cursor=tip)
        return public_payload(
            {
                "messages": [_message_view(item) for item in items],
                "next_cursor": next_cursor,
                "error_code": None,
                "error_text": None,
                **author,
            }
        )

    async def watch(
        self,
        remote_account_id: str,
        peer_id: str,
        username: str | None = None,
        source: SourceMessageContext | None = None,
        *,
        initial_cursor: str | None = None,
    ) -> None:
        async with self._lock(remote_account_id):
            meta = self._require_active(remote_account_id)
            peers = list(meta.get("watched_peers") or [])
            if peer_id not in peers:
                peers.append(peer_id)
                meta["watched_peers"] = peers
            cursors = dict(meta.get("cursors") or {})
            if source is not None:
                meta.setdefault("peer_sources", {})[peer_id] = {
                    "chat_id": source.chat_id,
                    "message_id": source.message_id,
                }
                usernames = meta.setdefault("peer_source_usernames", {})
                if source.chat_username is None:
                    usernames.pop(peer_id, None)
                else:
                    usernames[peer_id] = source.chat_username
            if not self.push_updates and peer_id not in cursors:
                baseline = initial_cursor
                if baseline is None:
                    baseline = await self._latest_cursor(meta, peer_id, username, source)
                cursors[peer_id] = baseline
                meta["cursors"] = cursors
            self.store.save(meta)
            if self.auto_listen:
                if self.push_updates:
                    self._ensure_push(remote_account_id)
                else:
                    self.ensure_listener()

    def _mark_session_revoked(self, meta: dict[str, Any], error: SessionRevokedError) -> None:
        if meta.get("session_error"):
            return
        remote_account_id = str(meta["remote_account_id"])
        meta["session_error"] = error.code
        if self.push_updates:
            meta.setdefault("pending_events", {})[error.code] = {
                "event": error.code, "remote_account_id": remote_account_id,
            }
        self.store.save(meta)
        log.warning("client sessions: вход недействителен account=%s cause=%s",
                    remote_account_id, type(error.__cause__ or error).__name__)
        if self.auto_listen and self.push_updates:
            self._ensure_push(remote_account_id)
            self._push_signals[remote_account_id].set()

    def _ensure_push(self, remote_account_id: str) -> None:
        current = self._push_tasks.get(remote_account_id)
        if current is None or current.done():
            self._push_signals[remote_account_id] = asyncio.Event()
            self._push_tasks[remote_account_id] = asyncio.create_task(self._run_push(remote_account_id))

    async def _receive_update(
        self, remote_account_id: str, peer_id: str, username: str | None, item: HistoryItem,
    ) -> None:
        if item.outgoing:
            return
        async with self._lock(remote_account_id):
            meta = self.store.get(remote_account_id)
            if not meta or meta["status"] != "active" or meta.get("session_error"):
                return
            pending = meta.setdefault("pending_events", {})
            before = len(pending)
            for peer in meta.get("watched_peers") or []:
                if peer != peer_id and not self._matches_alias(remote_account_id, peer, peer_id, username):
                    continue
                key = f"{peer}:{item.telegram_message_id}"
                if key not in pending:
                    pending[key] = {
                        "remote_account_id": remote_account_id,
                        "peer_id": peer,
                        "telegram_message_id": str(item.telegram_message_id),
                        "text": item.text,
                        "occurred_at": _iso(item.occurred_at),
                        "direction": "incoming",
                    }
            if len(pending) != before:
                self.store.save(meta)
                signal = self._push_signals.get(remote_account_id)
                if signal is not None:
                    signal.set()

    def _matches_alias(
        self, remote_account_id: str, peer: str, peer_id: str, event_username: str | None,
    ) -> bool:
        if not peer.startswith("username:"):
            return False
        alias = peer.removeprefix("username:")
        if event_username:
            return alias.casefold() == event_username.casefold()
        return self.gateway.cached_peer_id(self.store.session_path(remote_account_id), alias) == peer_id

    async def _deliver_update(self, remote_account_id: str) -> bool:
        meta = self.store.get(remote_account_id) or {}
        pending = meta.get("pending_events") or {}
        if meta.get("status") != "active" or not pending:
            return False
        key, payload = next(iter(pending.items()))
        try:
            status = await self.webhook.send(payload)
        except Exception as exc:
            log.warning("client sessions: доставка не удалась account=%s cause=%s",
                        remote_account_id, type(exc).__name__)
            return False
        if not 200 <= status < 300:
            log.warning("client sessions: доставка не удалась account=%s status=%s", remote_account_id, status)
            return False
        async with self._lock(remote_account_id):
            fresh = self.store.get(remote_account_id) or {}
            current = fresh.get("pending_events") or {}
            if current.get(key) == payload:
                del current[key]
                self.store.save(fresh)
        return True

    async def _receive_loop(self, remote_account_id: str) -> None:
        async def receive(peer: str, username: str | None, item: HistoryItem) -> None:
            await self._receive_update(remote_account_id, peer, username, item)

        delay = 5.0
        while True:
            try:
                meta = self._require_active(remote_account_id)
            except SessionRevokedError:
                return
            started = time.monotonic()
            wait = delay
            try:
                await self.gateway.listen_for_messages(self.store.session_path(remote_account_id), receive)
            except SessionRevokedError as exc:
                async with self._lock(remote_account_id):
                    fresh = self.store.get(remote_account_id)
                    if fresh and fresh.get("status") == "active" and fresh.get("qr_session_id") == meta.get("qr_session_id"):
                        self._mark_session_revoked(fresh, exc)
                return
            except FloodWaitError as exc:
                wait = max(delay, exc.retry_after_seconds or 0)
            except Exception as exc:
                log.warning("client sessions: соединение оборвалось account=%s cause=%s",
                            remote_account_id, type(exc).__name__)
            connected_for = time.monotonic() - started
            await asyncio.sleep(wait)
            delay = 5.0 if connected_for >= 60 else min(delay * 2, 120)

    async def _run_push(self, remote_account_id: str) -> None:
        signal = self._push_signals[remote_account_id]
        receive = asyncio.create_task(self._receive_loop(remote_account_id))
        try:
            while True:
                signal.clear()
                meta = self.store.get(remote_account_id) or {}
                if meta.get("session_error") and not receive.done():
                    receive.cancel()
                    await asyncio.gather(receive, return_exceptions=True)
                if await self._deliver_update(remote_account_id):
                    continue
                meta = self.store.get(remote_account_id) or {}
                if meta.get("status") != "active" or (receive.done() and not meta.get("pending_events")):
                    return
                try:
                    await asyncio.wait_for(signal.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
        finally:
            receive.cancel()
            await asyncio.gather(receive, return_exceptions=True)

    async def poll_once(self) -> list[dict[str, Any]]:
        delivered: list[dict[str, Any]] = []
        for meta in self.store.list_watched():
            remote_account_id = str(meta["remote_account_id"])
            if meta.get("status") != "active":
                continue
            for peer in meta.get("watched_peers") or []:
                async with self._lock(remote_account_id):
                    fresh = self.store.get(remote_account_id)
                    if (not fresh or fresh.get("status") != "active" or fresh.get("session_error")
                            or self._remaining_history_wait(fresh)):
                        break
                    if peer not in (fresh.get("watched_peers") or []):
                        continue
                    cursors = dict(fresh.get("cursors") or {})
                    sources = fresh.get("peer_sources") or {}
                    source_usernames = fresh.get("peer_source_usernames") or {}
                    username = peer.removeprefix("username:") if peer.startswith("username:") else None
                    peer_id = None if username else peer
                    source = (
                        SourceMessageContext(**sources[peer], chat_username=source_usernames.get(peer))
                        if peer in sources else None
                    )
                    cursor = str(cursors.get(peer) or "0")
                    try:
                        items, _next = await self.gateway.get_history(
                            self.store.session_path(remote_account_id),
                            peer_id,
                            username,
                            cursor,
                            20,
                            True,
                            source,
                        )
                    except FloodWaitError as exc:
                        self._pause_history(fresh, exc)
                        break
                    except SessionRevokedError as exc:
                        self._mark_session_revoked(fresh, exc)
                        break
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
                    if last_ok != cursor:
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
        if not self.auto_listen:
            return
        watched = self.store.list_watched()
        if self.push_updates:
            for meta in watched:
                self._ensure_push(str(meta["remote_account_id"]))
        elif watched:
            self.ensure_listener()

    async def stop(self) -> None:
        self._stop.set()
        for task in self._push_tasks.values():
            task.cancel()
        await asyncio.gather(*self._push_tasks.values(), return_exceptions=True)
        self._push_tasks.clear()
        self._push_signals.clear()
        if self._listener:
            self._listener.cancel()
            try:
                await self._listener
            except asyncio.CancelledError:
                pass
            self._listener = None
        if isinstance(self.gateway, TelethonGateway):
            await self.gateway.close()

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
        meta: dict[str, Any],
        peer_id: str,
        username: str | None,
        source: SourceMessageContext | None,
    ) -> str:
        if self._remaining_history_wait(meta):
            return "0"
        numeric = None if peer_id.startswith("username:") else peer_id
        name = username or (peer_id.removeprefix("username:") if peer_id.startswith("username:") else None)
        try:
            items, _next = await self.gateway.get_history(
                self.store.session_path(meta["remote_account_id"]),
                numeric,
                name,
                None,
                1,
                False,
                source,
            )
        except FloodWaitError as exc:
            self._pause_history(meta, exc)
            return "0"
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
        if not meta or meta.get("status") != "active" or meta.get("session_error"):
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
    webhook = WebhookNotifier()
    configured = webhook.configured()
    push_requested = os.getenv("CLIENT_SESSION_PUSH_ENABLED", "").strip().lower() == "true"
    if push_requested and not configured:
        log.error("client sessions: для событий Telegram нужен адрес webhook и секрет")
    push_updates = push_requested and configured
    return ClientSessionService(
        FileSessionStore(root),
        TelethonGateway(push_updates=push_updates),
        webhook,
        qr_ttl_seconds=ttl,
        poll_seconds=poll,
        auto_listen=configured,
        push_updates=push_updates,
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
