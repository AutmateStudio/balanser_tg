"""D7 — чтение/запись assigned_account_id в source_channels (ТЗ §5.1, A8)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app_balance.queue.db import acquire


_GET_ASSIGNED_SQL = """
SELECT assigned_account_id
FROM source_channels
WHERE id = $1
"""

_SET_ASSIGNED_SQL = """
UPDATE source_channels
SET assigned_account_id = $2
WHERE id = $1
RETURNING id
"""

_CLEAR_ASSIGNED_SQL = """
UPDATE source_channels
SET assigned_account_id = NULL
WHERE id = $1
RETURNING id
"""

_FIND_BY_REF_SQL = """
SELECT id
FROM source_channels
WHERE external_url ILIKE '%' || $1 || '%'
   OR name ILIKE '%' || $1 || '%'
ORDER BY id DESC
LIMIT 1
"""

_LIST_PENDING_COLLECT_SQL = """
SELECT id, assigned_account_id
FROM source_channels
WHERE assigned_account_id IS NOT NULL
  AND extra_data_collected = false
ORDER BY created_at ASC, id ASC
LIMIT $1
"""

_LIST_STALE_FOR_UPDATE_SQL = """
SELECT id, assigned_account_id, last_updated_at
FROM source_channels
WHERE assigned_account_id IS NOT NULL
  AND is_active = true
  AND (last_updated_at IS NULL
       OR last_updated_at < now() - ($1 * interval '1 second'))
ORDER BY last_updated_at ASC NULLS FIRST
LIMIT $2
"""

_COUNT_BY_ACCOUNTS_SQL = """
SELECT assigned_account_id, COUNT(*) AS cnt
FROM source_channels
WHERE assigned_account_id = ANY($1::bigint[])
GROUP BY assigned_account_id
"""

_LIST_FOR_ACCOUNT_SQL = """
SELECT id, external_url, external_channel_id
FROM source_channels
WHERE assigned_account_id = $1
ORDER BY created_at ASC, id ASC
LIMIT $2
"""

_LIST_ASSIGNED_DETAIL_SQL = """
SELECT id, name, external_url, external_channel_id, is_active,
       extra_data_collected, last_updated_at
FROM source_channels
WHERE assigned_account_id = $1
ORDER BY created_at ASC, id ASC
LIMIT $2
"""

_GET_COLLECT_TARGET_SQL = """
SELECT id, external_url, external_channel_id
FROM source_channels
WHERE id = $1
"""

# F6: метаданные сбора → metadata.extra_data (jsonb merge) + флаг собранности.
_SAVE_EXTRA_DATA_SQL = """
UPDATE source_channels
SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
    extra_data_collected = true
WHERE id = $1
RETURNING id
"""

# F7: обновление метаданных + last_updated_at, без флага extra_data_collected.
_SAVE_CHANNEL_UPDATE_SQL = """
UPDATE source_channels
SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
    name = COALESCE($3, name),
    last_updated_at = now()
WHERE id = $1
RETURNING id
"""


@dataclass(frozen=True, slots=True)
class PendingChannel:
    """Канал-кандидат для collect_extra_data (F4, §23 ТЗ)."""

    channel_id: int
    account_id: int


@dataclass(frozen=True, slots=True)
class StaleChannel:
    """Канал с устаревшими метаданными — кандидат для update_channel (F5/F7, §24 ТЗ)."""

    id: int
    account_id: int
    last_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class AssignedChannelDetail:
    """Канал, закреплённый за аккаунтом (PG read API)."""

    id: int
    name: str | None
    external_url: str | None
    external_channel_id: str | None
    is_active: bool
    extra_data_collected: bool
    last_updated_at: datetime | None

    def ref(self) -> str:
        url = (self.external_url or "").strip()
        if url:
            return url
        ext = (self.external_channel_id or "").strip()
        if ext and not ext.startswith("@"):
            return f"@{ext}"
        return ext


@dataclass(frozen=True, slots=True)
class ChannelRef:
    """Канал для продюсера балансировки (F2): id + ссылки для payload move_channel."""

    id: int
    external_url: str | None
    external_channel_id: str | None

    def ref(self) -> str:
        """channel_ref для payload: external_url, fallback на external_channel_id."""
        url = (self.external_url or "").strip()
        if url:
            return url
        return (self.external_channel_id or "").strip()


@dataclass(frozen=True, slots=True)
class CollectTarget:
    """Канал-цель multi-op пайплайна (F6/F7): id + ссылки для resolve ref."""

    id: int
    external_url: str | None
    external_channel_id: str | None

    def ref(self) -> str:
        """channel_ref для get_entity: external_url, fallback external_channel_id."""
        url = (self.external_url or "").strip()
        if url:
            return url
        return (self.external_channel_id or "").strip()


class SourceChannelsRepo:
    async def get_assigned_account(self, channel_id: int) -> int | None:
        async with acquire() as conn:
            return await conn.fetchval(_GET_ASSIGNED_SQL, channel_id)

    async def set_assigned_account(self, channel_id: int, account_id: int) -> bool:
        async with acquire() as conn:
            row = await conn.fetchrow(_SET_ASSIGNED_SQL, channel_id, account_id)
            return row is not None

    async def clear_assigned_account(self, channel_id: int) -> bool:
        async with acquire() as conn:
            row = await conn.fetchrow(_CLEAR_ASSIGNED_SQL, channel_id)
            return row is not None

    async def find_id_by_ref(self, ref: str) -> int | None:
        needle = (ref or "").strip().lstrip("@")
        if not needle:
            return None
        async with acquire() as conn:
            val = await conn.fetchval(_FIND_BY_REF_SQL, needle)
            return int(val) if val is not None else None

    async def list_pending_collect(self, limit: int) -> list[PendingChannel]:
        """Каналы с assigned_account_id и extra_data_collected=false (F4)."""
        if limit <= 0:
            return []
        async with acquire() as conn:
            rows = await conn.fetch(_LIST_PENDING_COLLECT_SQL, limit)
        return [
            PendingChannel(
                channel_id=int(row["id"]),
                account_id=int(row["assigned_account_id"]),
            )
            for row in rows
        ]

    async def list_stale_for_update(
        self, limit: int, stale_after_seconds: int
    ) -> list[StaleChannel]:
        """F5: каналы с устаревшим last_updated_at (приоритет старым, §24 ТЗ).

        Никогда не обновлявшиеся (last_updated_at IS NULL) идут первыми
        (ORDER BY ... NULLS FIRST). Использует idx_source_channels_stale_update.
        """
        if limit <= 0:
            return []
        async with acquire() as conn:
            rows = await conn.fetch(
                _LIST_STALE_FOR_UPDATE_SQL, stale_after_seconds, limit
            )
        return [
            StaleChannel(
                id=int(row["id"]),
                account_id=int(row["assigned_account_id"]),
                last_updated_at=row["last_updated_at"],
            )
            for row in rows
        ]

    async def count_channels_by_accounts(
        self, account_ids: list[int]
    ) -> dict[int, int]:
        """F2: число закреплённых каналов на каждый аккаунт.

        Аккаунты без каналов в ответ не попадают (нули добиваются вызывающим).
        """
        if not account_ids:
            return {}
        async with acquire() as conn:
            rows = await conn.fetch(_COUNT_BY_ACCOUNTS_SQL, account_ids)
        return {int(row["assigned_account_id"]): int(row["cnt"]) for row in rows}

    async def list_channels_for_account(
        self, account_id: int, limit: int
    ) -> list[ChannelRef]:
        """F2: каналы аккаунта (для выбора кандидатов на перенос)."""
        if limit <= 0:
            return []
        async with acquire() as conn:
            rows = await conn.fetch(_LIST_FOR_ACCOUNT_SQL, account_id, limit)
        return [
            ChannelRef(
                id=int(row["id"]),
                external_url=row["external_url"],
                external_channel_id=row["external_channel_id"],
            )
            for row in rows
        ]

    async def list_assigned_detail_for_account(
        self, account_id: int, limit: int = 500
    ) -> list[AssignedChannelDetail]:
        """PG: каналы с assigned_account_id = account_id (read API дашборда)."""
        if limit <= 0:
            return []
        async with acquire() as conn:
            rows = await conn.fetch(_LIST_ASSIGNED_DETAIL_SQL, account_id, limit)
        return [
            AssignedChannelDetail(
                id=int(row["id"]),
                name=row["name"],
                external_url=row["external_url"],
                external_channel_id=row["external_channel_id"],
                is_active=bool(row["is_active"]),
                extra_data_collected=bool(row["extra_data_collected"]),
                last_updated_at=row["last_updated_at"],
            )
            for row in rows
        ]

    async def count_assigned_by_account(self, account_id: int) -> int:
        async with acquire() as conn:
            val = await conn.fetchval(
                "SELECT COUNT(*) FROM source_channels WHERE assigned_account_id = $1",
                account_id,
            )
        return int(val or 0)

    async def get_collect_target(self, channel_id: int) -> CollectTarget | None:
        """F6/F7: ссылки канала для resolve ref в multi-op пайплайне."""
        async with acquire() as conn:
            row = await conn.fetchrow(_GET_COLLECT_TARGET_SQL, channel_id)
        if row is None:
            return None
        return CollectTarget(
            id=int(row["id"]),
            external_url=row["external_url"],
            external_channel_id=row["external_channel_id"],
        )

    async def save_extra_data(self, channel_id: int, signals: dict[str, Any]) -> bool:
        """F6: merge сигналов в metadata + extra_data_collected=true."""
        async with acquire() as conn:
            row = await conn.fetchrow(
                _SAVE_EXTRA_DATA_SQL, channel_id, json.dumps(signals)
            )
        return row is not None

    async def save_channel_update(
        self, channel_id: int, signals: dict[str, Any]
    ) -> bool:
        """F7: merge сигналов в metadata, синхронизация name, last_updated_at=now()."""
        title = None
        extra = signals.get("extra_data") if isinstance(signals, dict) else None
        if isinstance(extra, dict):
            title = extra.get("title")
        async with acquire() as conn:
            row = await conn.fetchrow(
                _SAVE_CHANNEL_UPDATE_SQL, channel_id, json.dumps(signals), title
            )
        return row is not None
