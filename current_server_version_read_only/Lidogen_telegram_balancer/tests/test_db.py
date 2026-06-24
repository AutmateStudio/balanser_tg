"""B1 — интеграционные и unit-тесты app_balance.queue.db."""
from __future__ import annotations

import pytest

from app_balance.queue import db
from tests.conftest import requires_pg


def test_get_dsn_raises_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="QUEUE_DATABASE_URL"):
        db._get_dsn()


@pytest.mark.asyncio
async def test_get_pool_raises_before_init() -> None:
    await db.close_pool()
    with pytest.raises(RuntimeError, match="не инициализирован"):
        await db.get_pool()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_healthcheck(pg_pool) -> None:
    assert await db.healthcheck() is True


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_init_pool_idempotent(pg_pool) -> None:
    pool_before = await db.get_pool()
    await db.init_pool()
    pool_after = await db.get_pool()
    assert pool_before is pool_after


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_transaction_commit_and_rollback(pg_pool) -> None:
    await db.verify_transaction_rollback()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_acquire_returns_connection(pg_pool) -> None:
    async with db.acquire() as conn:
        val = await conn.fetchval("SELECT 42")
    assert val == 42
