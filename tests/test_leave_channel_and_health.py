"""Приёмка: LeaveChannel, fencing/watchdog, /health, /discover, QR, deny."""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app_balance.queue.accounts import _PAIR_LOCK_SQL
from app_balance.queue.channel_leave import leave_tracked_channel
from app_balance.queue.task_queue import _MARK_STUCK_TIMED_OUT_SQL


class FloodWaitError(Exception):
    pass


class UserNotParticipantError(Exception):
    pass


class _Entity:
    def __init__(self, entity_id: int) -> None:
        self.id = entity_id


class _Full:
    def __init__(self, linked_chat_id: int | None) -> None:
        self.full_chat = SimpleNamespace(linked_chat_id=linked_chat_id)


class _Client:
    def __init__(
        self,
        *,
        resolve_error: Exception | None = None,
        linked_chat_id: int | None = None,
        leave_errors: list[Exception] | None = None,
        full_error: Exception | None = None,
    ) -> None:
        self.resolve_error = resolve_error
        self.linked_chat_id = linked_chat_id
        self.leave_errors = list(leave_errors or [])
        self.full_error = full_error
        self.left: list[object] = []
        self.resolved: list[object] = []

    async def get_entity(self, ref: object) -> _Entity:
        self.resolved.append(ref)
        if self.resolve_error is not None and not isinstance(ref, int):
            raise self.resolve_error
        return _Entity(2 if isinstance(ref, int) else 1)

    async def __call__(self, request: object) -> object:
        name = type(request).__name__
        if name == "GetFullChannelRequest":
            if self.full_error is not None:
                raise self.full_error
            return _Full(self.linked_chat_id)
        if name == "LeaveChannelRequest":
            if self.leave_errors:
                raise self.leave_errors.pop(0)
            self.left.append(getattr(request, "channel"))
            return None
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_leave_channel_and_linked_discussion() -> None:
    client = _Client(linked_chat_id=99)
    outcome = await leave_tracked_channel(client, "@demo")
    assert outcome.ok is True
    assert outcome.left_count == 2
    assert outcome.soft_ok is False
    assert 99 in client.resolved
    assert len(client.left) == 2


@pytest.mark.asyncio
async def test_leave_soft_ok_when_not_a_member() -> None:
    client = _Client(resolve_error=UserNotParticipantError("not a participant"))
    outcome = await leave_tracked_channel(client, "@gone")
    assert outcome.ok is True
    assert outcome.soft_ok is True
    assert outcome.left_count == 0


@pytest.mark.asyncio
async def test_leave_propagates_flood_wait() -> None:
    client = _Client(resolve_error=FloodWaitError("wait"))
    with pytest.raises(FloodWaitError):
        await leave_tracked_channel(client, "@demo")


@pytest.mark.asyncio
async def test_leave_continues_when_get_full_fails() -> None:
    client = _Client(full_error=RuntimeError("full failed"))
    outcome = await leave_tracked_channel(client, "@demo")
    assert outcome.left_count == 1


def test_watchdog_sql_keeps_live_lock() -> None:
    sql = _MARK_STUCK_TIMED_OUT_SQL.lower()
    assert "locked_until <= now()" in sql
    assert "direct_lease" in sql


def test_pair_lock_orders_by_id() -> None:
    sql = _PAIR_LOCK_SQL.lower()
    assert sql.index("order by id") < sql.index("for update")


def test_move_channel_does_not_call_leave() -> None:
    from discovery_api.session_registry import SessionClump

    strip_src = inspect.getsource(SessionClump._strip_channel_local)
    move_src = inspect.getsource(SessionClump.move_channel)
    assert "LeaveChannelRequest" not in strip_src
    assert "leave_channel(" not in move_src
    assert "_strip_channel_local" in move_src


def test_metrics_migration_counts_retry_and_canonical_rph() -> None:
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1] / "DB" / "A24_leave_channel_and_metrics.sql"
    ).read_text(encoding="utf-8")
    assert "'queued', 'scheduled', 'retry'" in sql
    assert "channels.JoinChannel" in sql and "65" in sql
    assert "get_entity" in sql and "200" in sql


@pytest.mark.asyncio
async def test_health_live_always_ok() -> None:
    from discovery_api.main import health_live

    body = await health_live()
    assert body["status"] == "в порядке"


@pytest.mark.asyncio
async def test_health_503_when_postgres_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from discovery_api.main import health

    monkeypatch.setattr("discovery_api.main.get_use_pg_queue", lambda: True)
    monkeypatch.delenv("HEALTH_VPN_IFACE", raising=False)

    class _Boom:
        async def __aenter__(self):
            raise ConnectionError("pg down")

        async def __aexit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr("app_balance.queue.db.acquire", lambda: _Boom())
    monkeypatch.setattr(
        "app_balance.queue.worker_heartbeat.worker_heartbeat_problem",
        lambda: None,
    )

    response = await health()
    assert response.status_code == 503
    assert "postgres" in response.body.decode()


@pytest.mark.asyncio
async def test_discover_without_task_id_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    from discovery_api.router import DiscoveryRequest, discover

    monkeypatch.setattr("discovery_api.router.get_use_pg_queue", lambda: True)

    async def no_task(**_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(task_id=None, action_id="act")

    monkeypatch.setattr("discovery_api.router.enqueue_telegram_discover", no_task)
    req = DiscoveryRequest(query="мебель", session_name="missing")
    with pytest.raises(HTTPException) as exc:
        await discover(req, async_mode=True)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_send_bot_message_uses_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    from discovery_api.router import BotMessageRequest, send_bot_message, send_bot_message_endpoint

    seen: list[object] = []

    async def fake_to_thread(fn: object, *args: object) -> SimpleNamespace:
        seen.append(fn)
        seen.extend(args)
        return SimpleNamespace(message_id=9, chat=SimpleNamespace(id=5))

    monkeypatch.setattr("discovery_api.router.asyncio.to_thread", fake_to_thread)
    response = await send_bot_message_endpoint(BotMessageRequest(chat_id=5, text="привет"))
    assert response.ok is True
    assert seen[0] is send_bot_message


@pytest.mark.asyncio
async def test_qr_success_disconnects_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from discovery_api.auth import QRSession, _wait_for_scan

    me = SimpleNamespace(phone="700", id=1, first_name="Ann", last_name="")
    client = AsyncMock()
    client.get_me = AsyncMock(return_value=me)
    client.session.save = lambda: "session-string"
    client.disconnect = AsyncMock()
    qr = AsyncMock()
    qr.wait = AsyncMock(return_value=object())
    session = QRSession(
        session_id="sid",
        client=client,
        qr_login=qr,
        qr_url="tg://login",
        session_name=None,
    )
    monkeypatch.setattr("discovery_api.auth.upsert_session", lambda **_kwargs: None)

    await _wait_for_scan(session)

    assert session.status == "success"
    client.disconnect.assert_awaited()


def test_detach_denied_account_does_not_move_channels() -> None:
    from scripts.quarantine_account import detach_session_from_jobs

    records = [
        {
            "parser_id": "p1",
            "session_name_list": ["fgsh123", "other"],
            "assignments": {"https://t.me/keep": "fgsh123", "https://t.me/stay": "other"},
            "channel_list": ["https://t.me/keep", "https://t.me/stay"],
        }
    ]
    updated, removed = detach_session_from_jobs(records, "fgsh123")
    assert updated[0]["session_name_list"] == ["other"]
    assert updated[0]["assignments"] == {"https://t.me/stay": "other"}
    assert "https://t.me/keep" in removed["channels"]
    assert "https://t.me/keep" not in updated[0]["channel_list"]


def test_bot_polling_off_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from discovery_api.config import bot_polling_enabled

    monkeypatch.setenv("BOT_POLLING_ENABLED", "false")
    assert bot_polling_enabled() is False
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.delenv("BOT_POLLING_ENABLED", raising=False)
    assert bot_polling_enabled() is False
