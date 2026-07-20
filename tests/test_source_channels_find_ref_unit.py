"""Unit-тесты нормализации ref и порядка lookup в find_id_by_ref (без PG)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app_balance.queue.source_channels import (
    SourceChannelsRepo,
    _normalize_channel_ref_needle,
    _telegram_url_candidates,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ""),
        ("@Foo", "Foo"),
        ("  @bar  ", "bar"),
        ("https://t.me/MyChan", "MyChan"),
        ("https://t.me/MyChan?start=1", "MyChan"),
        ("http://telegram.me/x/#frag", "x"),
        ("t.me/plain", "plain"),
        ("-100123", "-100123"),
    ],
)
def test_normalize_channel_ref_needle(raw: str, expected: str) -> None:
    assert _normalize_channel_ref_needle(raw) == expected


def test_telegram_url_candidates_include_https_tme() -> None:
    urls = _telegram_url_candidates("mychan")
    assert "https://t.me/mychan" in urls
    assert all(u == u.lower() for u in urls)


@pytest.mark.asyncio
async def test_find_id_by_ref_prefers_external_channel_id() -> None:
    """Первый indexed lookup по external_channel_id — без ILIKE."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=[42])
    conn.transaction = MagicMock(return_value=AsyncMock())
    # acquire() — async context manager
    cm = AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = None

    with patch("app_balance.queue.source_channels.acquire", return_value=cm):
        found = await SourceChannelsRepo().find_id_by_ref("@MyChan")

    assert found == 42
    assert conn.fetchval.await_count == 1
    sql = conn.fetchval.await_args_list[0].args[0]
    assert "external_channel_id" in sql
    assert "ILIKE" not in sql


@pytest.mark.asyncio
async def test_find_id_by_ref_falls_back_to_name_then_url() -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=[None, None, 99])
    cm = AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = None

    with patch("app_balance.queue.source_channels.acquire", return_value=cm):
        found = await SourceChannelsRepo().find_id_by_ref("https://t.me/demo")

    assert found == 99
    assert conn.fetchval.await_count == 3
    third_sql = conn.fetchval.await_args_list[2].args[0]
    assert "external_url" in third_sql


@pytest.mark.asyncio
async def test_find_id_by_ref_empty_returns_none() -> None:
    with patch("app_balance.queue.source_channels.acquire") as acq:
        assert await SourceChannelsRepo().find_id_by_ref("   ") is None
        acq.assert_not_called()
