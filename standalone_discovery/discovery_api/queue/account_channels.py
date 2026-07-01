"""GET /queue/accounts/{session_name}/channels — каналы аккаунта из PG."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.source_channels import SourceChannelsRepo
from discovery_api.config import get_use_pg_queue

_accounts = AccountsRepo()
_channels = SourceChannelsRepo()
_DEFAULT_LIMIT = 500

class AccountChannelItemResponse(BaseModel):
    channel_id: int
    channel_ref: str
    name: str | None = None
    external_url: str | None = None
    is_active: bool
    extra_data_collected: bool
    last_updated_at: datetime | None = None


class AccountChannelsPgResponse(BaseModel):
    session_name: str
    account_id: int
    channel_count: int
    source: Literal["pg"] = "pg"
    channels: list[AccountChannelItemResponse] = Field(default_factory=list)


class AccountChannelsSummaryResponse(BaseModel):
    session_name: str
    account_id: int
    assigned_channel_count: int
    active_assigned_count: int
    pending_collect_count: int
    stale_update_count: int
    queue_status: str | None = None
    is_enabled: bool | None = None


def _guard_pg_queue() -> None:
    if not get_use_pg_queue():
        raise HTTPException(
            status_code=503,
            detail="PG-очередь не включена (USE_PG_QUEUE=false)",
        )


async def _resolve_account_id(session_name: str) -> int:
    account_id = await _accounts.get_id_by_session_name(session_name)
    if account_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"Аккаунт не найден в PG: session_name={session_name!r}",
        )
    return account_id


async def get_account_channels_pg(
    session_name: str,
    *,
    limit: int = _DEFAULT_LIMIT,
) -> AccountChannelsPgResponse:
    """Список каналов аккаунта из source_channels.assigned_account_id."""
    _guard_pg_queue()
    try:
        from app_balance.queue import db

        await db.init_pool()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    account_id = await _resolve_account_id(session_name)
    rows = await _channels.list_assigned_detail_for_account(account_id, limit=limit)
    items = [
        AccountChannelItemResponse(
            channel_id=row.id,
            channel_ref=row.ref(),
            name=row.name,
            external_url=row.external_url,
            is_active=row.is_active,
            extra_data_collected=row.extra_data_collected,
            last_updated_at=row.last_updated_at,
        )
        for row in rows
    ]
    return AccountChannelsPgResponse(
        session_name=session_name,
        account_id=account_id,
        channel_count=len(items),
        channels=items,
    )


async def get_account_channels_summary(session_name: str) -> AccountChannelsSummaryResponse:
    """Сводка по каналам аккаунта для дашборда (F4/F5 кандидаты)."""
    _guard_pg_queue()
    try:
        from app_balance.queue import db

        await db.init_pool()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    account_id = await _resolve_account_id(session_name)
    account = await _accounts.get_by_id(account_id)

    from app_balance.queue import db

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              COUNT(*) AS assigned_total,
              COUNT(*) FILTER (WHERE is_active = true) AS active_assigned,
              COUNT(*) FILTER (
                WHERE is_active = true AND extra_data_collected = false
              ) AS pending_collect,
              COUNT(*) FILTER (
                WHERE is_active = true
                  AND (
                    last_updated_at IS NULL
                    OR last_updated_at < now() - interval '30 days'
                  )
              ) AS stale_update
            FROM source_channels
            WHERE assigned_account_id = $1
            """,
            account_id,
        )

    return AccountChannelsSummaryResponse(
        session_name=session_name,
        account_id=account_id,
        assigned_channel_count=int(row["assigned_total"] or 0),
        active_assigned_count=int(row["active_assigned"] or 0),
        pending_collect_count=int(row["pending_collect"] or 0),
        stale_update_count=int(row["stale_update"] or 0),
        queue_status=account.status if account else None,
        is_enabled=account.is_enabled if account else None,
    )
