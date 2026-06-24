"""D3 — unit-тесты clump_bootstrap (без Telethon)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from discovery_api import clump_bootstrap


def test_env_telegram_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_ID", raising=False)
    monkeypatch.delenv("API_HASH", raising=False)
    assert clump_bootstrap.env_telegram_configured() is False

    monkeypatch.setenv("API_ID", "123")
    monkeypatch.setenv("API_HASH", "abc")
    assert clump_bootstrap.env_telegram_configured() is True


@pytest.mark.asyncio
async def test_restore_all_skips_when_persistence_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clump_bootstrap, "is_persistence_enabled", lambda: False)
    assert await clump_bootstrap.restore_all_clumps_from_store() == 0


@pytest.mark.asyncio
async def test_restore_all_skips_without_telegram_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clump_bootstrap, "is_persistence_enabled", lambda: True)
    monkeypatch.delenv("API_ID", raising=False)
    assert await clump_bootstrap.restore_all_clumps_from_store() == 0


@pytest.mark.asyncio
async def test_restore_all_restores_valid_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clump_bootstrap, "is_persistence_enabled", lambda: True)
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "hash")

    record = {
        "parser_id": "p-test",
        "session_name_list": ["/s1"],
        "webhook_url": "https://hook.example/",
        "channel_list": ["@ch"],
        "clump_name": "test",
    }
    fake_clump = AsyncMock()
    fake_clump.restore_from_record = lambda _rec: None

    with (
        patch.object(clump_bootstrap, "load_persisted_jobs", return_value=[record]),
        patch.object(clump_bootstrap, "normalize_persisted_record", side_effect=lambda r: r),
        patch.object(clump_bootstrap, "get_clump", return_value=None),
        patch.object(
            clump_bootstrap,
            "get_or_create_clump",
            new_callable=AsyncMock,
            return_value=fake_clump,
        ) as mock_create,
    ):
        count = await clump_bootstrap.restore_all_clumps_from_store()

    assert count == 1
    mock_create.assert_awaited_once()
    fake_clump.start.assert_awaited_once()
