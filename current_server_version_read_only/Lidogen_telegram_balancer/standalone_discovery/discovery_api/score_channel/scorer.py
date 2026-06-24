"""Публичный API скоринга: лидген + устаревшие функции (legacy)."""
from __future__ import annotations

from discovery_api.score_channel.legacy_scorer import (
    ChannelDiscoveryScore,
    GroupDiscoveryScore,
    _overlap_ratio,
    _tokenize,
    score_discovered_channel,
    score_discovered_group,
)
from discovery_api.score_channel.lidgen_scorer import (
    LidgenScore,
    score_channel_for_lidgen,
    score_group_for_lidgen,
)

__all__ = [
    "ChannelDiscoveryScore",
    "GroupDiscoveryScore",
    "LidgenScore",
    "score_channel_for_lidgen",
    "score_discovered_channel",
    "score_discovered_group",
    "score_group_for_lidgen",
]
