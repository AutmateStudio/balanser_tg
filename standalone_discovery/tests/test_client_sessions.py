from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from discovery_api.client_sessions.errors import FloodWaitError, PeerUnavailableError, PrivacyRestrictedError
from discovery_api.client_sessions.gateway import AuthProfile, HistoryItem
from discovery_api.client_sessions.router import router
from discovery_api.client_sessions.service import ClientSessionService, set_service
from discovery_api.client_sessions.store import FileSessionStore


ACCOUNT = "account01"
SECRET_PASSWORD = "horse-battery-secret"


class FakeHandle:
    def __init__(self, mode: str, profile: AuthProfile) -> None:
        self.qr_url = "tg://login?token=not-a-session"
        self.mode = mode
        self.profile = profile
        self.password_seen: str | None = None
        self._event = asyncio.Event()
        if mode in {"success", "password"}:
            self._event.set()

    async def wait(self) -> AuthProfile:
        await self._event.wait()
        if self.mode == "cancel":
            raise asyncio.CancelledError()
        if self.mode == "password":
            from discovery_api.client_sessions.errors import PasswordRequired

            raise PasswordRequired()
        if self.mode == "timeout":
            await asyncio.sleep(30)
        return self.profile

    async def submit_password(self, password: str) -> AuthProfile:
        self.password_seen = password
        return self.profile

    async def cancel(self) -> None:
        self.mode = "cancel"
        self._event.set()


class FakeGateway:
    def __init__(self) -> None:
        self.mode = "success"
        self.profile = AuthProfile(77, "ivan", "Иван")
        self.sent: list[str] = []
        self.send_error: Exception | None = None
        self.history: list[HistoryItem] = []
        self.handles: list[FakeHandle] = []
        self.revoked = False

    async def begin_qr(self, session_path: Path) -> FakeHandle:
        handle = FakeHandle(self.mode, self.profile)
        self.handles.append(handle)
        return handle

    async def send_dm(self, session_path: Path, peer_id: str | None, username: str | None, text: str) -> int:
        if self.send_error:
            raise self.send_error
        self.sent.append(text)
        return 100 + len(self.sent)

    async def get_history(
        self,
        session_path: Path,
        peer_id: str | None,
        username: str | None,
        cursor: str | None,
        limit: int,
        newer: bool,
    ) -> tuple[list[HistoryItem], str | None]:
        items = list(self.history)
        if cursor and cursor.isdigit():
            border = int(cursor)
            if newer:
                items = [item for item in items if item.telegram_message_id > border]
            else:
                items = [item for item in items if item.telegram_message_id < border]
        items.sort(key=lambda item: item.telegram_message_id)
        if newer:
            page = items[:limit]
            return page, None
        page = items[-limit:]
        next_cursor = str(page[0].telegram_message_id) if len(page) >= limit and page else None
        return page, next_cursor

    async def revoke(self, session_path: Path) -> None:
        self.revoked = True


class RecordingWebhook:
    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.status = 200

    async def send(self, payload: dict) -> int:
        self.payloads.append(payload)
        return self.status


def _service(tmp_path: Path, gateway: FakeGateway | None = None) -> tuple[ClientSessionService, FakeGateway, RecordingWebhook]:
    gateway = gateway or FakeGateway()
    webhook = RecordingWebhook()
    service = ClientSessionService(
        FileSessionStore(tmp_path / "client-sessions"),
        gateway,
        webhook,
        qr_ttl_seconds=0.05,
        auto_listen=False,
    )
    set_service(service)
    return service, gateway, webhook


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _assert_no_secrets(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in {"session_string", "sessionString", "session_file", "password"}
            _assert_no_secrets(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_secrets(item)


def test_qr_success_does_not_return_session_string(tmp_path: Path) -> None:
    service, _gateway, _webhook = _service(tmp_path)
    client = _client()
    created = client.post("/discovery-api/client-sessions/qr", json={"remote_account_id": ACCOUNT})
    assert created.status_code == 200, created.text
    _assert_no_secrets(created.json())
    status = client.get(f"/discovery-api/client-sessions/qr/{created.json()['qr_session_id']}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "success"
    assert body["telegram_user_id"] == 77
    _assert_no_secrets(body)
    meta = (tmp_path / "client-sessions" / f"{ACCOUNT}.json").read_text(encoding="utf-8")
    assert "session_string" not in meta
    assert service.store.get(ACCOUNT)["status"] == "active"
    set_service(None)


def test_2fa_password_is_not_stored(tmp_path: Path) -> None:
    _service_obj, gateway, _webhook = _service(tmp_path)
    gateway.mode = "password"
    client = _client()
    created = client.post("/discovery-api/client-sessions/qr", json={"remote_account_id": ACCOUNT})
    qr_id = created.json()["qr_session_id"]
    assert client.get(f"/discovery-api/client-sessions/qr/{qr_id}").json()["status"] == "password_required"
    done = client.post(
        f"/discovery-api/client-sessions/qr/{qr_id}/2fa",
        json={"password": SECRET_PASSWORD},
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "success"
    _assert_no_secrets(done.json())
    meta = (tmp_path / "client-sessions" / f"{ACCOUNT}.json").read_text(encoding="utf-8")
    assert SECRET_PASSWORD not in meta
    assert gateway.handles[-1].password_seen == SECRET_PASSWORD
    set_service(None)


@pytest.mark.asyncio
async def test_cancel_qr(tmp_path: Path) -> None:
    service, gateway, _webhook = _service(tmp_path)
    gateway.mode = "wait"
    created = await service.open_qr(ACCOUNT)
    task = asyncio.create_task(service.await_qr(created["qr_session_id"]))
    await asyncio.sleep(0.01)
    await service.cancel_qr(created["qr_session_id"])
    await task
    assert service.qr_view(created["qr_session_id"])["status"] == "canceled"
    set_service(None)


def test_send_is_idempotent_on_flood_wait(tmp_path: Path) -> None:
    _service_obj, gateway, _webhook = _service(tmp_path)
    client = _client()
    created = client.post("/discovery-api/client-sessions/qr", json={"remote_account_id": ACCOUNT})
    assert client.get(f"/discovery-api/client-sessions/qr/{created.json()['qr_session_id']}").json()["status"] == "success"
    gateway.send_error = FloodWaitError(30)
    body = {
        "peer_id": "555",
        "text": "Здравствуйте",
        "idempotency_key": "idem-key-1",
    }
    first = client.post(f"/discovery-api/client-sessions/{ACCOUNT}/messages", json=body)
    second = client.post(f"/discovery-api/client-sessions/{ACCOUNT}/messages", json=body)
    assert first.status_code == 200
    assert first.json()["error_code"] == "flood_wait"
    assert first.json()["retry_after_seconds"] == 30
    assert second.json() == first.json()
    assert gateway.sent == []
    _assert_no_secrets(first.json())
    set_service(None)


def test_privacy_and_peer_errors(tmp_path: Path) -> None:
    _service_obj, gateway, _webhook = _service(tmp_path)
    client = _client()
    created = client.post("/discovery-api/client-sessions/qr", json={"remote_account_id": ACCOUNT})
    client.get(f"/discovery-api/client-sessions/qr/{created.json()['qr_session_id']}")
    gateway.send_error = PrivacyRestrictedError()
    privacy = client.post(
        f"/discovery-api/client-sessions/{ACCOUNT}/messages",
        json={"peer_id": "555", "text": "Привет", "idempotency_key": "idem-priv-1"},
    )
    assert privacy.json()["error_code"] == "privacy_restricted"
    gateway.send_error = PeerUnavailableError()
    peer = client.post(
        f"/discovery-api/client-sessions/{ACCOUNT}/messages",
        json={"username": "missing_user", "text": "Привет", "idempotency_key": "idem-peer-1"},
    )
    assert peer.json()["error_code"] == "peer_unavailable"
    set_service(None)


def test_history_pagination(tmp_path: Path) -> None:
    _service_obj, gateway, _webhook = _service(tmp_path)
    now = datetime.now(timezone.utc)
    gateway.history = [
        HistoryItem(1, "старое", now, False),
        HistoryItem(2, "среднее", now, True),
        HistoryItem(3, "новое", now, False),
    ]
    client = _client()
    created = client.post("/discovery-api/client-sessions/qr", json={"remote_account_id": ACCOUNT})
    client.get(f"/discovery-api/client-sessions/qr/{created.json()['qr_session_id']}")
    page = client.get(
        f"/discovery-api/client-sessions/{ACCOUNT}/history",
        params={"peer_id": "555", "limit": 2},
    )
    assert page.status_code == 200, page.text
    body = page.json()
    assert [item["telegram_message_id"] for item in body["messages"]] == ["2", "3"]
    assert body["next_cursor"] == "2"
    older = client.get(
        f"/discovery-api/client-sessions/{ACCOUNT}/history",
        params={"peer_id": "555", "limit": 2, "cursor": body["next_cursor"]},
    )
    assert [item["text"] for item in older.json()["messages"]] == ["старое"]
    assert older.json()["next_cursor"] is None
    set_service(None)


@pytest.mark.asyncio
async def test_listener_forwards_only_new_incoming(tmp_path: Path) -> None:
    service, gateway, webhook = _service(tmp_path)
    now = datetime.now(timezone.utc)
    gateway.history = [HistoryItem(10, "уже было", now, False)]
    await service.open_qr(ACCOUNT)
    await service.await_qr((service.store.get(ACCOUNT) or {})["qr_session_id"])
    await service.watch(ACCOUNT, "555", None)
    gateway.history.append(HistoryItem(11, "ответ лида", now, False))
    gateway.history.append(HistoryItem(12, "наше", now, True))
    delivered = await service.poll_once()
    assert [item["text"] for item in delivered] == ["ответ лида"]
    assert webhook.payloads[0]["peer_id"] == "555"
    assert "session_string" not in webhook.payloads[0]
    set_service(None)


def test_revoked_session_does_not_send(tmp_path: Path) -> None:
    _service_obj, gateway, _webhook = _service(tmp_path)
    client = _client()
    created = client.post("/discovery-api/client-sessions/qr", json={"remote_account_id": ACCOUNT})
    client.get(f"/discovery-api/client-sessions/qr/{created.json()['qr_session_id']}")
    revoked = client.delete(f"/discovery-api/client-sessions/{ACCOUNT}")
    assert revoked.status_code == 200
    assert gateway.revoked is True
    blocked = client.post(
        f"/discovery-api/client-sessions/{ACCOUNT}/messages",
        json={"peer_id": "555", "text": "ещё раз", "idempotency_key": "idem-revoked"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "session_revoked"
    set_service(None)


def test_client_api_does_not_enroll_parser() -> None:
    root = Path(__file__).resolve().parents[1] / "discovery_api" / "client_sessions"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "register_account_after_qr" not in text
    main = (Path(__file__).resolve().parents[1] / "discovery_api" / "main.py").read_text(encoding="utf-8")
    assert "client_router" in main
