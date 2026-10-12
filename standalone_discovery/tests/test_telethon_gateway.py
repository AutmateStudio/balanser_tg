from __future__ import annotations

import asyncio
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from discovery_api.client_sessions.errors import SessionBusyError, SessionRevokedError
from discovery_api.client_sessions.telethon_gateway import TelethonGateway


class FakeTelegramClient:
    instances: list["FakeTelegramClient"] = []
    connect_error: BaseException | None = None

    def __init__(self, session: str, _api_id: int, _api_hash: str) -> None:
        self.session = session
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        type(self).instances.append(self)

    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connect_calls += 1
        await asyncio.sleep(0)
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False


@pytest.fixture(autouse=True)
def fake_telethon(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeTelegramClient.instances = []
    FakeTelegramClient.connect_error = None
    monkeypatch.setenv("API_ID", "123")
    monkeypatch.setenv("API_HASH", "hash")
    module = types.ModuleType("telethon")
    module.TelegramClient = FakeTelegramClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "telethon", module)


@pytest.mark.asyncio
async def test_concurrent_connect_uses_one_client(tmp_path: Path) -> None:
    gateway = TelethonGateway()
    session = tmp_path / "account.session"

    clients = await asyncio.gather(
        gateway._client(session),
        gateway._client(session),
    )

    assert clients[0] is clients[1]
    assert len(FakeTelegramClient.instances) == 1
    assert FakeTelegramClient.instances[0].connect_calls == 1


@pytest.mark.asyncio
async def test_disconnected_client_is_reconnected_without_new_sqlite_client(tmp_path: Path) -> None:
    gateway = TelethonGateway()
    session = tmp_path / "account.session"

    client = await gateway._client(session)
    client.connected = False
    reconnected = await gateway._client(session)

    assert reconnected is client
    assert len(FakeTelegramClient.instances) == 1
    assert client.connect_calls == 2


@pytest.mark.asyncio
async def test_fresh_client_disconnects_previous_one(tmp_path: Path) -> None:
    gateway = TelethonGateway()
    session = tmp_path / "account.session"

    previous = await gateway._client(session)
    current = await gateway._client(session, fresh=True)

    assert current is not previous
    assert previous.disconnect_calls == 1
    assert len(FakeTelegramClient.instances) == 2


@pytest.mark.asyncio
async def test_database_lock_is_reported_as_temporary_session_busy(tmp_path: Path) -> None:
    FakeTelegramClient.connect_error = sqlite3.OperationalError("database is locked")
    gateway = TelethonGateway()

    with pytest.raises(SessionBusyError):
        await gateway._client(tmp_path / "account.session")


@pytest.mark.asyncio
async def test_duplicated_auth_key_requires_rebind(tmp_path: Path) -> None:
    FakeTelegramClient.connect_error = type("AuthKeyDuplicatedError", (Exception,), {})()
    gateway = TelethonGateway()

    with pytest.raises(SessionRevokedError):
        await gateway._client(tmp_path / "account.session")
