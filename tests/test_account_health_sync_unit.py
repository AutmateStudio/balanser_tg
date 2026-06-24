"""D6 — unit-тесты account_health_sync (без Telethon, без обязательной PG)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app_balance.queue import account_health_sync as sync


def test_pg_health_sync_enabled_requires_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUEUE_DATABASE_URL", raising=False)
    assert sync.pg_health_sync_enabled() is False

    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    assert sync.pg_health_sync_enabled() is True


@pytest.mark.asyncio
async def test_persist_flood_cooldown_noop_without_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QUEUE_DATABASE_URL", raising=False)
    with patch.object(sync._repo, "set_cooldown", new_callable=AsyncMock) as mock_cd:
        await sync.persist_flood_cooldown("/s1", 60)
    mock_cd.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_flood_cooldown_calls_set_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    with (
        patch.object(sync, "_ensure_pool", new_callable=AsyncMock, return_value=True),
        patch.object(sync._repo, "set_cooldown", new_callable=AsyncMock, return_value=True) as mock_cd,
    ):
        await sync.persist_flood_cooldown("  /sess  ", 120)

    mock_cd.assert_awaited_once()
    args = mock_cd.await_args.args
    assert args[0] == "/sess"
    assert isinstance(args[1], datetime)
    assert args[1].tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_persist_flood_cooldown_swallows_repo_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    with (
        patch.object(sync, "_ensure_pool", new_callable=AsyncMock, return_value=True),
        patch.object(
            sync._repo,
            "set_cooldown",
            new_callable=AsyncMock,
            side_effect=RuntimeError("pg down"),
        ),
    ):
        await sync.persist_flood_cooldown("/s1", 30)


@pytest.mark.asyncio
async def test_persist_banned_noop_empty_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    with patch.object(sync._repo, "set_banned", new_callable=AsyncMock) as mock_ban:
        await sync.persist_banned("   ", "reason")
    mock_ban.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_banned_calls_set_banned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    with (
        patch.object(sync, "_ensure_pool", new_callable=AsyncMock, return_value=True),
        patch.object(sync._repo, "set_banned", new_callable=AsyncMock, return_value=True) as mock_ban,
    ):
        await sync.persist_banned("/s2", "UserDeactivated")

    mock_ban.assert_awaited_once_with("/s2", reason="UserDeactivated")
