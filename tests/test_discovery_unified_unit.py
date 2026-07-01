"""discover_unified_on_client — дедуп каналов и групп."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from discovery_api.discovery import (
    DiscoveredChannel,
    DiscoveredGroup,
    DiscoveryResult,
    GroupDiscoveryResult,
    discover_unified_on_client,
)


@pytest.mark.asyncio
async def test_discover_unified_dedups_groups_already_in_channels() -> None:
    shared_peer = -100555
    channel_result = DiscoveryResult(
        query="test",
        channels=[
            DiscoveredChannel(
                peer_id=shared_peer,
                title="Shared",
                username="shared",
                participants_count=100,
                depth=0,
                source="search",
                meta={"megagroup": True},
            ),
            DiscoveredChannel(
                peer_id=-100111,
                title="Channel",
                username="ch",
                participants_count=50,
                depth=0,
                source="search",
                meta={"broadcast": True},
            ),
        ],
        total=2,
        depth_stats={0: 2},
    )
    group_result = GroupDiscoveryResult(
        query="test",
        seeds=["seed1"],
        groups=[
            DiscoveredGroup(
                peer_id=shared_peer,
                title="Shared dup",
                username="shared",
                participants_count=100,
                depth=0,
                source="contacts",
                matched_seed="seed1",
            ),
            DiscoveredGroup(
                peer_id=-100222,
                title="Extra group",
                username="grp",
                participants_count=20,
                depth=1,
                source="global",
                matched_seed="seed1",
            ),
        ],
        total=2,
        depth_stats={0: 1, 1: 1},
        errors=["minor"],
    )

    with patch(
        "discovery_api.discovery._discover",
        new_callable=AsyncMock,
        return_value=channel_result,
    ), patch(
        "discovery_api.discovery._discover_groups",
        new_callable=AsyncMock,
        return_value=group_result,
    ):
        result = await discover_unified_on_client(MagicMock(), "test")

    assert len(result.channels) == 2
    assert len(result.groups) == 1
    assert result.groups[0].peer_id == -100222
    assert result.total == 3
    assert result.seeds == ["seed1"]
    assert result.errors == ["minor"]
    assert result.depth_stats[0] == 3
    assert result.depth_stats[1] == 1
