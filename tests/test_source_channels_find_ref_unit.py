"""Unit-тесты нормализации ref и порядка lookup в find_id_by_ref (без PG)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app_balance.queue.source_channels import (
    _MAX_INDIVIDUAL_FALLBACK_REFS,
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


@pytest.mark.asyncio
async def test_find_ids_by_refs_batch_ext_id() -> None:
    """Тир 1 (external_channel_id) закрывает все refs одним fetch."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"needle": "a", "id": 1},
            {"needle": "b", "id": 2},
        ]
    )
    cm = AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = None

    with patch("app_balance.queue.source_channels.acquire", return_value=cm):
        found = await SourceChannelsRepo().find_ids_by_refs(["@a", "@b"])

    assert found == {"@a": 1, "@b": 2}
    assert conn.fetch.await_count == 1
    sql = conn.fetch.await_args_list[0].args[0]
    assert "external_channel_id" in sql


@pytest.mark.asyncio
async def test_find_ids_by_refs_falls_back_to_name_and_url() -> None:
    """Непойманные тиром 1 идут в тир 2 (name), затем тир 3 (url exact)."""
    conn = AsyncMock()

    async def _fetch(sql, *args):
        if "external_channel_id" in sql:
            return []
        if "trim(both '@'" in sql or "trim(both '@' from" in sql:
            return [{"needle": "named", "id": 10}]
        if "external_url" in sql:
            return [{"url": "https://t.me/urled", "id": 20}]
        return []

    conn.fetch = AsyncMock(side_effect=_fetch)
    cm = AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = None

    with patch("app_balance.queue.source_channels.acquire", return_value=cm), patch.object(
        SourceChannelsRepo, "find_id_by_ref", new_callable=AsyncMock
    ) as mock_single:
        mock_single.return_value = None
        found = await SourceChannelsRepo().find_ids_by_refs(
            ["@named", "https://t.me/urled"]
        )

    assert found == {"@named": 10, "https://t.me/urled": 20}
    # Тир 1 + тир 2 + тир 3
    assert conn.fetch.await_count == 3
    # Fallback find_id_by_ref не вызывался — всё закрыто batch-тирами
    mock_single.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_ids_by_refs_fallback_per_unresolved() -> None:
    """Остаток после тиров 1–3 уходит в поштучный find_id_by_ref."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    cm = AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = None

    with patch("app_balance.queue.source_channels.acquire", return_value=cm), patch.object(
        SourceChannelsRepo, "find_id_by_ref", new_callable=AsyncMock, return_value=77
    ) as mock_single:
        found = await SourceChannelsRepo().find_ids_by_refs(["@rare"])

    assert found == {"@rare": 77}
    mock_single.assert_awaited_once_with("@rare")


@pytest.mark.asyncio
async def test_find_ids_by_refs_caps_individual_fallback() -> None:
    """Bulk remainder (> лимита) не уходит в неограниченный поштучный fallback.

    Защита от таймаута на массовых add/remove (сотни-тысячи каналов):
    find_id_by_ref (до 1.5с на ILIKE-тир) вызывается не более
    _MAX_INDIVIDUAL_FALLBACK_REFS раз за вызов find_ids_by_refs, остаток
    остаётся без channel_id (лениво дорезолвится при выполнении задачи).
    """
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    cm = AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = None

    refs = [f"@rare{i}" for i in range(_MAX_INDIVIDUAL_FALLBACK_REFS + 10)]

    with patch("app_balance.queue.source_channels.acquire", return_value=cm), patch.object(
        SourceChannelsRepo, "find_id_by_ref", new_callable=AsyncMock, return_value=1
    ) as mock_single:
        found = await SourceChannelsRepo().find_ids_by_refs(refs)

    assert mock_single.await_count == _MAX_INDIVIDUAL_FALLBACK_REFS
    assert len(found) == _MAX_INDIVIDUAL_FALLBACK_REFS


@pytest.mark.asyncio
async def test_find_ids_by_refs_empty() -> None:
    with patch("app_balance.queue.source_channels.acquire") as acq:
        assert await SourceChannelsRepo().find_ids_by_refs(["", "  "]) == {}
        acq.assert_not_called()
