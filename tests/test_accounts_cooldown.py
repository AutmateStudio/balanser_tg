"""B7 — set_cooldown / set_banned и влияние на pick."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from tests.conftest import requires_pg

_PREFIX = "test_b7_"


@pytest.fixture
async def cooldown_account(pg_pool):
    session_name = f"{_PREFIX}{uuid.uuid4().hex}"

    async def _cleanup() -> None:
        async with db.acquire() as conn:
            await conn.execute(
                "DELETE FROM accounts WHERE session_name LIKE $1", f"{_PREFIX}%"
            )

    await _cleanup()

    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )

    yield session_name, account_id
    await _cleanup()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_set_cooldown_excludes_from_pick(cooldown_account) -> None:
    session_name, account_id = cooldown_account
    repo = AccountsRepo()
    until = datetime.now(timezone.utc) + timedelta(hours=1)

    assert await repo.set_cooldown(session_name, until) is True

    async with db.acquire() as conn:
        pickable = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM accounts
                WHERE id = $1
                  AND status IN ('active', 'cooldown')
                  AND is_enabled = true
                  AND current_task_id IS NULL
                  AND (cooldown_until IS NULL OR cooldown_until <= now())
            )
            """,
            account_id,
        )
        row = await conn.fetchrow(
            "SELECT status, cooldown_until FROM accounts WHERE id = $1",
            account_id,
        )
    assert pickable is False
    assert row["status"] == "cooldown"
    assert row["cooldown_until"] is not None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_set_cooldown_extends_existing_until(cooldown_account) -> None:
    session_name, account_id = cooldown_account
    repo = AccountsRepo()
    short = datetime.now(timezone.utc) + timedelta(minutes=5)
    long = datetime.now(timezone.utc) + timedelta(hours=2)

    await repo.set_cooldown(session_name, short)
    await repo.set_cooldown(session_name, long)

    async with db.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT cooldown_until FROM accounts WHERE id = $1", account_id
        )
    assert stored >= long.replace(microsecond=0) - timedelta(seconds=1)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_set_cooldown_unknown_session_returns_false(pg_pool) -> None:
    repo = AccountsRepo()
    until = datetime.now(timezone.utc) + timedelta(minutes=10)
    assert await repo.set_cooldown(f"{_PREFIX}missing_{uuid.uuid4().hex}", until) is False


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_set_banned_excludes_from_pick(cooldown_account) -> None:
    session_name, account_id = cooldown_account
    repo = AccountsRepo()

    assert await repo.set_banned(session_name, reason="UserDeactivatedBanError") is True

    async with db.acquire() as conn:
        pickable = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM accounts
                WHERE id = $1
                  AND status IN ('active', 'cooldown')
                  AND is_enabled = true
                  AND current_task_id IS NULL
                  AND (cooldown_until IS NULL OR cooldown_until <= now())
            )
            """,
            account_id,
        )
        row = await conn.fetchrow(
            "SELECT status, last_error, cooldown_until FROM accounts WHERE id = $1",
            account_id,
        )
    assert pickable is False
    assert row["status"] == "banned"
    assert row["last_error"] == "UserDeactivatedBanError"
    assert row["cooldown_until"] is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_cooldown_pickable_with_cooldown_status(cooldown_account) -> None:
    session_name, account_id = cooldown_account
    repo = AccountsRepo()
    until = datetime.now(timezone.utc) - timedelta(minutes=1)

    await repo.set_cooldown(session_name, until)

    async with db.acquire() as conn:
        pickable = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM accounts
                WHERE id = $1
                  AND status IN ('active', 'cooldown')
                  AND is_enabled = true
                  AND current_task_id IS NULL
                  AND (cooldown_until IS NULL OR cooldown_until <= now())
            )
            """,
            account_id,
        )
    assert pickable is True
