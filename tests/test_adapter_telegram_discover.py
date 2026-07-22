"""Adapter — telegram_discover task type."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app_balance.queue.accounts import Account
from app_balance.queue.adapter import execute_task
from app_balance.queue.discover_persist import PersistStats
from app_balance.queue.task_queue import ClaimedTask


@pytest.mark.asyncio
async def test_execute_telegram_discover_persists_and_writes_result() -> None:
    from discovery_api.discovery import DiscoveredChannel, UnifiedDiscoveryResult

    task = ClaimedTask(
        id=7,
        task_type_id=1,
        task_type_code="telegram_discover",
        priority=80,
        payload={
            "query": "test",
            "first_pass_limit": 10,
            "similarity_depth": 1,
            "include_global_search": True,
            "include_groups": True,
        },
        channel_id=None,
        account_id=42,
        source_account_id=None,
        target_account_id=None,
        attempt_count=1,
        max_attempts=5,
        dedup_key="td:1",
        locked_by="w",
        locked_until=None,
    )
    account = Account(
        id=42,
        session_name="Client1",
        status="active",
        is_enabled=True,
        current_task_id=7,
        cooldown_until=None,
        last_used_at=None,
    )
    mock_client = MagicMock()
    mock_queue = AsyncMock()
    mock_queue.merge_payload = AsyncMock(return_value=True)
    mock_channels_repo = AsyncMock()

    discovery_result = UnifiedDiscoveryResult(
        query="test",
        channels=[
            DiscoveredChannel(
                peer_id=100,
                title="Broadcast",
                username="b",
                participants_count=50,
                depth=0,
                source="search",
                score_signals={"linked_chat_id": 200},
                meta={"broadcast": True},
            )
        ],
        total=1,
        depth_stats={0: 1},
    )
    persist_stats = PersistStats(inserted=1, updated=0, skipped_no_discussion=0, channel_ids=[501])

    with patch(
        "discovery_api.discovery.discover_unified_on_client",
        new_callable=AsyncMock,
        return_value=discovery_result,
    ) as mock_run, patch(
        "discovery_api.discovery.persist_unified_discovery",
        new_callable=AsyncMock,
        return_value=persist_stats,
    ) as mock_persist:
        await execute_task(
            task,
            account=account,
            client_getter=AsyncMock(return_value=mock_client),
            queue=mock_queue,
            channels_repo=mock_channels_repo,
        )

    mock_run.assert_awaited_once()
    mock_persist.assert_awaited_once()
    mock_queue.merge_payload.assert_awaited_once()
    patch_payload = mock_queue.merge_payload.await_args.args[1]
    assert patch_payload["result"]["persist"]["inserted"] == 1
    assert patch_payload["result"]["total"] == 1
