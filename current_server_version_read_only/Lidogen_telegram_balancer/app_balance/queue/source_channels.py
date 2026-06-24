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


class SourceChannelsRepo:
    async def get_assigned_account(self, channel_id: int) -> int | None:
        async with acquire() as conn:
            return await conn.fetchval(_GET_ASSIGNED_SQL, channel_id)

    async def set_assigned_account(self, channel_id: int, account_id: int) -> bool:
        async with acquire() as conn:
            row = await conn.fetchrow(_SET_ASSIGNED_SQL, channel_id, account_id)
            return row is not None
