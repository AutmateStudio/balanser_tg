"""D7 — integration-тесты SourceChannelsRepo."""
from __future__ import annotations

import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.source_channels import SourceChannelsRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_d7_repo_"


async def _cleanup() -> None:
    async with db.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM account_resource_usage
            WHERE task_id IN (
                SELECT tq.id FROM task_queue tq
                JOIN source_channels sc ON sc.id = tq.channel_id
                WHERE sc.external_channel_id LIKE $1
            )
            """,
            f"{_PREFIX}%",
        )
        await conn.execute(
            """
            DELETE FROM task_queue
            WHERE channel_id IN (
                SELECT id FROM source_channels WHERE external_channel_id LIKE $1
            )
            """,
            f"{_PREFIX}%",
        )
        await conn.execute(
            """
            UPDATE source_channels
            SET assigned_account_id = NULL
            WHERE external_channel_id LIKE $1
            """,
            f"{_PREFIX}%",
        )
        await conn.execute(
            "DELETE FROM source_channels WHERE external_channel_id LIKE $1",
            f"{_PREFIX}%",
        )
        await conn.execute(
            "DELETE FROM platforms WHERE code LIKE $1",
            f"{_PREFIX}%",
        )
    await cleanup_queue_test_data(session_name_like=f"{_PREFIX}%")


@pytest.fixture
async def source_channel_row(pg_pool):
    await _cleanup()
    suffix = uuid.uuid4().hex
    platform_code = f"{_PREFIX}{suffix}"
    external_id = f"{_PREFIX}{suffix}"
    session_name = f"{_PREFIX}{suffix}"

    async with db.acquire() as conn:
        platform_id = await conn.fetchval(
            "INSERT INTO platforms (code, name) VALUES ($1, $2) RETURNING id",
            platform_code,
            "D7 test platform",
        )
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )
        channel_id = await conn.fetchval(
            "INSERT INTO source_channels (platform_id, external_channel_id, name) "
            "VALUES ($1, $2, $3) RETURNING id",
            platform_id,
            external_id,
            "test channel",
        )

    yield {
        "channel_id": channel_id,
        "account_id": account_id,
    }
    await _cleanup()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_set_assigned_account_updates_row(source_channel_row) -> None:
    ctx = source_channel_row
    repo = SourceChannelsRepo()

    ok = await repo.set_assigned_account(ctx["channel_id"], ctx["account_id"])
    assert ok is True

    assigned = await repo.get_assigned_account(ctx["channel_id"])
    assert assigned == ctx["account_id"]


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_set_assigned_account_unknown_id_returns_false(pg_pool) -> None:
    repo = SourceChannelsRepo()
    ok = await repo.set_assigned_account(9_999_999_999, 1)
    assert ok is False


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_assigned_account(source_channel_row) -> None:
    ctx = source_channel_row
    repo = SourceChannelsRepo()

    assert await repo.get_assigned_account(ctx["channel_id"]) is None

    await repo.set_assigned_account(ctx["channel_id"], ctx["account_id"])
    assert await repo.get_assigned_account(ctx["channel_id"]) == ctx["account_id"]
