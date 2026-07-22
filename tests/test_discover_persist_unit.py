"""Фильтр discussion и маппинг discover → source_channels."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app_balance.queue.discover_persist import (
    build_upsert_fields,
    should_persist_discovered,
)


@dataclass
class _FakeItem:
    peer_id: int
    title: str
    username: str | None = None
    participants_count: int | None = None
    depth: int = 0
    source: str = "search"
    score_total: int = 0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    score_signals: dict[str, Any] = field(default_factory=dict)
    score_hard_flags: dict[str, bool] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    matched_seed: str | None = None


def test_should_persist_broadcast_with_discussion() -> None:
    item = _FakeItem(
        peer_id=1,
        title="Ch",
        meta={"broadcast": True},
        score_signals={"linked_chat_id": -100222},
    )
    assert should_persist_discovered(item) is True


def test_should_skip_broadcast_without_discussion() -> None:
    item = _FakeItem(
        peer_id=2,
        title="Ch",
        meta={"broadcast": True},
        score_signals={"linked_chat_id": None},
    )
    assert should_persist_discovered(item) is False


def test_should_persist_megagroup_always() -> None:
    item = _FakeItem(
        peer_id=3,
        title="Group",
        meta={"megagroup": True, "broadcast": False},
        score_signals={},
    )
    assert should_persist_discovered(item) is True


def test_build_upsert_fields_external_id() -> None:
    item = _FakeItem(
        peer_id=-1001234567890,
        title="Test",
        username="testch",
        score_signals={"about": "about text", "linked_chat_id": 999},
        meta={"broadcast": True},
        score_total=42,
    )
    fields = build_upsert_fields(item)
    assert fields["external_channel_id"] == "-1001234567890"
    assert fields["external_url"] == "https://t.me/testch"
    assert fields["metadata"]["has_discussion"] is True
    assert fields["metadata"]["score"] == 42
