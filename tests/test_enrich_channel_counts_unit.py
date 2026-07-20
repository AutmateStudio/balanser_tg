"""Unit: enrich_channel_counts_from_pg — один batch вместо N+1."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from discovery_api.queue.account_queue_overlay import enrich_channel_counts_from_pg


@pytest.mark.asyncio
async def test_enrich_skips_when_pg_queue_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USE_PG_QUEUE", "false")
    rows = [{"session_name": "A", "channel_count": 0, "in_clump": False}]
    with patch("app_balance.queue.db.init_pool", new_callable=AsyncMock) as init:
        await enrich_channel_counts_from_pg(rows)
    init.assert_not_awaited()
    assert rows[0]["channel_count"] == 0


@pytest.mark.asyncio
async def test_enrich_batch_updates_only_when_pg_higher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USE_PG_QUEUE", "true")
    rows = [
        {"session_name": "Keep", "channel_count": 5, "in_clump": True},  # skip
        {"session_name": "Low", "channel_count": 1, "in_clump": False},
        {"session_name": "Empty", "channel_count": 0, "in_clump": True},
    ]

    accounts = AsyncMock()
    accounts.get_ids_by_session_names = AsyncMock(
        return_value={"Low": 10, "Empty": 20}
    )
    channels = AsyncMock()
    channels.count_channels_by_accounts = AsyncMock(
        return_value={10: 3, 20: 7}  # Low: 3>1, Empty: 7>0
    )

    with patch(
        "app_balance.queue.db.init_pool", new_callable=AsyncMock
    ), patch(
        "app_balance.queue.accounts.AccountsRepo", return_value=accounts
    ), patch(
        "app_balance.queue.source_channels.SourceChannelsRepo", return_value=channels
    ):
        await enrich_channel_counts_from_pg(rows)

    assert rows[0]["channel_count"] == 5  # не трогали
    assert rows[1]["channel_count"] == 3
    assert rows[2]["channel_count"] == 7
    accounts.get_ids_by_session_names.assert_awaited_once()
    channels.count_channels_by_accounts.assert_awaited_once_with([10, 20])
