"""D7 — чтение/запись assigned_account_id в source_channels (ТЗ §5.1, A8)."""
from __future__ import annotations

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
