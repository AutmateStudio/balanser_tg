"""Локальные unit-тесты цепочки «неавторизованная сессия» (без PG, без Telethon).

Проверяем теоретическую модель:
  get_or_create_client → map_telethon_exception → dispatch → notify_session_unauthorized
  → SessionHealth.error + accounts.status=error → UI last_error/status.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest
import telethon.errors as te

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "standalone_discovery")))

from app_balance.queue.error_codes import ErrorCode
from app_balance.queue.errors import PermanentError, map_clump_error_message, map_telethon_exception
from discovery_api.session_health import (
    SessionHealth,
    SessionStatus,
    classify_telethon_error,
    is_session_unauthorized_error,
)

_UNAUTH_MSG = (
    "Сессия '/app/sessions/test4' не авторизована; "
    "войдите в аккаунт для этой session"
)


# --- 1. Классификация ошибок ---


@pytest.mark.parametrize(
    "exc,expected_kind",
    [
        (RuntimeError(_UNAUTH_MSG), "unauthorized"),
        (te.UserDeactivatedError(None), "banned"),
        (te.SessionRevokedError(None), "banned"),
        (ConnectionError("network"), "transient"),
        (RuntimeError("boom"), "fatal"),
    ],
)
def test_classify_telethon_error_kinds(exc: BaseException, expected_kind: str) -> None:
    kind, _seconds = classify_telethon_error(exc)
    assert kind == expected_kind


def test_is_session_unauthorized_distinguishes_ban_from_missing_login() -> None:
    assert is_session_unauthorized_error(RuntimeError(_UNAUTH_MSG)) is True
    assert is_session_unauthorized_error(te.UserDeactivatedError(None)) is False
    assert is_session_unauthorized_error(te.SessionRevokedError(None)) is False


def test_map_layers_agree_on_account_unauthorized_code() -> None:
    exc = RuntimeError(_UNAUTH_MSG)
    from_telethon = map_telethon_exception(exc)
    from_clump = map_clump_error_message(_UNAUTH_MSG)

    assert isinstance(from_telethon, PermanentError)
    assert isinstance(from_clump, PermanentError)
    assert from_telethon.code == ErrorCode.ACCOUNT_UNAUTHORIZED
    assert from_clump.code == ErrorCode.ACCOUNT_UNAUTHORIZED


# --- 2. In-memory health (UI status) ---


def test_mark_unauthorized_sets_error_status_and_blocks_balancing() -> None:
    health = SessionHealth()
    assert health.status == SessionStatus.STARTING
    assert health.is_available() is True

    health.mark_unauthorized(_UNAUTH_MSG)

    assert health.status == SessionStatus.ERROR
    assert health.last_error == _UNAUTH_MSG
    assert health.connected is False
    assert health.is_available() is False


# --- 3. session_registry: notify + get_or_create_client ---


@pytest.mark.asyncio
async def test_notify_session_unauthorized_updates_health_and_pg() -> None:
    from discovery_api import session_registry as sr

    sr.reset_for_tests()
    try:
        clump = sr.SessionClump(["/app/sessions/test4"], "prod-main", webhook_url="http://h")
        sr._clumps["pid"] = clump
        pc = clump.parser_client_list[0]
        persist_mock = AsyncMock()

        with patch.object(sr, "_persist_unauthorized_pg", persist_mock):
            await sr.notify_session_unauthorized("/app/sessions/test4", _UNAUTH_MSG)

        assert pc.health.status == SessionStatus.ERROR
        assert pc.health.last_error == _UNAUTH_MSG
        persist_mock.assert_awaited_once_with("/app/sessions/test4", _UNAUTH_MSG)
    finally:
        await sr.release_all()
        sr.reset_for_tests()


@pytest.mark.asyncio
async def test_get_or_create_client_unauthorized_propagates_to_clump() -> None:
    from discovery_api import session_registry as sr

    class UnauthorizedClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            return None

        async def is_user_authorized(self) -> bool:
            return False

    sr.reset_for_tests()
    try:
        clump = sr.SessionClump(["/app/sessions/Test3"], "prod-main", webhook_url="http://h")
        sr._clumps["pid"] = clump
        pc = clump.parser_client_list[0]

        with (
            patch.object(sr, "TelegramClient", UnauthorizedClient),
            patch.object(sr, "get_api_id", return_value=1),
            patch.object(sr, "get_api_hash", return_value="hash"),
            patch.object(sr, "_persist_unauthorized_pg", new_callable=AsyncMock),
        ):
            with pytest.raises(RuntimeError, match="не авторизована"):
                await sr.get_or_create_client("/app/sessions/Test3")

        assert pc.health.status == SessionStatus.ERROR
        assert "не авторизована" in (pc.health.last_error or "")
    finally:
        await sr.release_all()
        sr.reset_for_tests()


def test_account_summary_exposes_last_error_for_ui() -> None:
    from discovery_api import session_registry as sr

    clump = sr.SessionClump(["/app/sessions/test4"], "prod-main", webhook_url="http://h")
    pc = clump.parser_client_list[0]
    pc.health.mark_unauthorized(_UNAUTH_MSG)

    summary = clump.account_summary(pc)

    assert summary["status"] == SessionStatus.ERROR
    assert summary["last_error"] == _UNAUTH_MSG


def test_pc_available_false_when_unauthorized() -> None:
    from discovery_api import session_registry as sr

    clump = sr.SessionClump(["/app/sessions/test4"], "prod-main", webhook_url="http://h")
    pc = clump.parser_client_list[0]
    pc.health.mark_unauthorized(_UNAUTH_MSG)

    assert clump._pc_available(pc) is False


# --- 4. account_registry: merged list для админки ---


@dataclass
class _FakeJob:
    clump: object


def test_list_all_accounts_merged_shows_runtime_error() -> None:
    from discovery_api import session_registry as sr
    from discovery_api.account_registry import list_all_accounts_merged

    clump = sr.SessionClump(["/app/sessions/Test3"], "prod-main", webhook_url="http://h")
    pc = clump.parser_client_list[0]
    pc.health.mark_unauthorized(_UNAUTH_MSG)

    jobs = {"pid": _FakeJob(clump=clump)}

    with (
        patch("discovery_api.account_registry.sync_accounts_from_disk"),
        patch("discovery_api.account_registry.list_accounts", return_value=[]),
        patch("discovery_api.account_registry.scan_sessions_dir", return_value=["Test3"]),
        patch("discovery_api.account_registry.get_account", return_value=None),
        patch("discovery_api.account_registry.session_file_exists", return_value=True),
        patch("discovery_api.account_registry.upsert_account", return_value={"display_name": "Test3"}),
    ):
        rows = list_all_accounts_merged(jobs)

    row = next(r for r in rows if r["session_name"] == "Test3")
    assert row["status"] == SessionStatus.ERROR
    assert row["last_error"] == _UNAUTH_MSG


# --- 5. dispatch: typed error → notify (как в prod worker) ---


@pytest.mark.asyncio
async def test_dispatch_runtime_error_maps_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    from app_balance.queue.dispatch import DispatchResult
    from app_balance.queue.mock_adapter import MockTaskAdapter
    from tests.test_dispatch import FakeAccounts, _claimed, _dispatcher, _fake_queue, _task_type, FakeTaskTypes

    notified: list[tuple[str, str]] = []

    async def _notify(session_name: str, message: str) -> None:
        notified.append((session_name, message))

    monkeypatch.setattr(
        "discovery_api.session_registry.notify_session_unauthorized",
        _notify,
    )

    class UnauthorizedAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise map_telethon_exception(RuntimeError(_UNAUTH_MSG))

    dispatcher = _dispatcher(
        _fake_queue(),
        FakeAccounts(),
        FakeTaskTypes(_task_type()),
        UnauthorizedAdapter(),
    )

    result = await dispatcher.dispatch(_claimed(16458, account_id=99))

    assert result == DispatchResult.FAILED
    assert notified == [("sess_99", _UNAUTH_MSG)]


@pytest.mark.asyncio
async def test_dispatch_unauthorized_fallback_pg_when_notify_unavailable() -> None:
    from app_balance.queue.dispatch import DispatchResult
    from app_balance.queue.errors import PermanentError
    from app_balance.queue.mock_adapter import MockTaskAdapter
    from tests.test_dispatch import FakeAccounts, _claimed, _dispatcher, _fake_queue, _task_type, FakeTaskTypes

    accounts = FakeAccounts()

    class UnauthorizedAdapter(MockTaskAdapter):
        async def execute(self, task, *, account):  # type: ignore[override]
            raise PermanentError(ErrorCode.ACCOUNT_UNAUTHORIZED, _UNAUTH_MSG)

    dispatcher = _dispatcher(
        _fake_queue(),
        accounts,
        FakeTaskTypes(_task_type()),
        UnauthorizedAdapter(),
    )

    with patch(
        "discovery_api.session_registry.notify_session_unauthorized",
        side_effect=ImportError("no discovery"),
    ):
        result = await dispatcher.dispatch(_claimed(16458, account_id=99))

    assert result == DispatchResult.FAILED
    assert accounts.account_errors == [("sess_99", _UNAUTH_MSG)]


# --- 6. PG-слой (mock): set_account_error исключает из pick ---


@pytest.mark.asyncio
async def test_persist_unauthorized_writes_error_status() -> None:
    from app_balance.queue import account_health_sync as sync

    with (
        patch.object(sync, "_ensure_pool", new_callable=AsyncMock, return_value=True),
        patch.object(
            sync._repo,
            "set_account_error",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_set,
    ):
        await sync.persist_unauthorized("/app/sessions/test4", _UNAUTH_MSG)

    mock_set.assert_awaited_once_with("/app/sessions/test4", reason=_UNAUTH_MSG)
