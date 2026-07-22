"""D7 — upsert discovered channels в source_channels."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app_balance.queue import db
from app_balance.queue.discover_persist import build_upsert_fields
from app_balance.queue.source_channels import SourceChannelsRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_upsert_disc_"


async def _cleanup() -> None:
    async with db.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM source_channels
            WHERE platform_id IN (
                SELECT id FROM platforms WHERE code LIKE $1
            )
            """,
            f"{_PREFIX}%",
        )
        await conn.execute(
            "DELETE FROM platforms WHERE code LIKE $1",
            f"{_PREFIX}%",
        )
    await cleanup_queue_test_data(session_name_like=f"{_PREFIX}%")


@pytest.fixture
async def platform_id(pg_pool) -> int:
    await _cleanup()
    suffix = uuid.uuid4().hex[:8]
    async with db.acquire() as conn:
        return int(
            await conn.fetchval(
                "INSERT INTO platforms (code, name) VALUES ($1, $2) RETURNING id",
                f"{_PREFIX}{suffix}",
                "Test TG",
            )
        )


@dataclass
class _Item:
    peer_id: int
    title: str
    username: str | None
    participants_count: int | None
    depth: int
    source: str
    score_total: int
    score_breakdown: dict = field(default_factory=dict)
    score_signals: dict[str, Any] = field(default_factory=dict)
    score_hard_flags: dict = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


@pytest.mark.asyncio
@requires_pg
async def test_upsert_insert_then_update(platform_id: int) -> None:
    peer = int(f"9{uuid.uuid4().int % 10**12}")
    item = _Item(
        peer_id=peer,
        title="First",
        username="ch1",
        participants_count=10,
        depth=0,
        source="search",
        score_total=5,
        score_signals={"about": "a1", "linked_chat_id": 1},
        meta={"broadcast": True},
    )
    fields = build_upsert_fields(item)
    repo = SourceChannelsRepo()

    first = await repo.upsert_discovered(platform_id=platform_id, **fields)
    assert first is not None
    assert first.inserted is True

    item.title = "Updated"
    fields2 = build_upsert_fields(item)
    second = await repo.upsert_discovered(platform_id=platform_id, **fields2)
    assert second is not None
    assert second.inserted is False
    assert second.channel_id == first.channel_id

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name, metadata FROM source_channels WHERE id = $1",
            first.channel_id,
        )
    assert row["name"] == "Updated"
    meta = row["metadata"]
    if isinstance(meta, str):
        import json

        meta = json.loads(meta)
    assert meta.get("score") == 5

    await _cleanup()


@pytest.mark.asyncio
@requires_pg
async def test_batch_upsert_skips_broadcast_without_discussion(platform_id: int) -> None:
    from app_balance.queue.discover_persist import (
        PersistStats,
        build_upsert_fields,
        should_persist_discovered,
    )

    items = [
        _Item(
            peer_id=int(f"8{uuid.uuid4().int % 10**12}"),
            title="No discussion",
            username=None,
            participants_count=1,
            depth=0,
            source="search",
            score_total=1,
            meta={"broadcast": True},
            score_signals={"linked_chat_id": None},
        ),
        _Item(
            peer_id=int(f"8{uuid.uuid4().int % 10**12}"),
            title="Group",
            username="grp",
            participants_count=5,
            depth=0,
            source="contacts",
            score_total=2,
            meta={"megagroup": True},
        ),
    ]
    repo = SourceChannelsRepo()
    stats: PersistStats = await repo.batch_upsert_discovered(
        items,
        platform_id=platform_id,
        should_persist=should_persist_discovered,
        build_fields=build_upsert_fields,
    )
    assert stats.inserted == 1
    assert stats.skipped_no_discussion == 1

    await _cleanup()
