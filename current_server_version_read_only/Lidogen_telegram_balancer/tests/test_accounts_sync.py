"""A10 — интеграционные тесты sync accounts → PG."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from app_balance.queue import db
from app_balance.queue.accounts_sync import SyncConfig, sync_accounts_to_pg
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_a10_"


def _init_sqlite(path: Path, rows: list[tuple[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE telegram_accounts (
                session_name TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                max_channels INTEGER,
                admin_blocked INTEGER NOT NULL DEFAULT 0,
                block_reason TEXT,
                source TEXT NOT NULL DEFAULT 'import',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        for session_name, admin_blocked in rows:
            conn.execute(
                "INSERT INTO telegram_accounts (session_name, admin_blocked) VALUES (?, ?)",
                (session_name, admin_blocked),
            )
        conn.commit()


def _write_parser_jobs(path: Path, session_names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "parser_id": "test_parser",
                    "session_name_list": session_names,
                }
            ]
        ),
        encoding="utf-8",
    )


def _touch_sessions(sessions_dir: Path, names: list[str]) -> None:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (sessions_dir / f"{name}.session").write_text("", encoding="utf-8")


@pytest.fixture
async def a10_cleanup(pg_pool):
    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_PREFIX}%",
            session_name_like=f"{_PREFIX}%",
        )
        async with db.acquire() as conn:
            await conn.execute(
                "DELETE FROM task_types WHERE code LIKE $1", f"{_PREFIX}%"
            )

    await _cleanup()
    yield _cleanup
    await _cleanup()


def _make_config(tmp_path: Path, suffix: str) -> tuple[SyncConfig, list[str]]:
    uid = uuid.uuid4().hex[:8]
    names = [f"{_PREFIX}{suffix}_{uid}_{i}" for i in range(3)]
    sqlite_path = tmp_path / suffix / "accounts.db"
    sessions_dir = tmp_path / suffix / "sessions"
    parser_path = tmp_path / suffix / "parser_jobs.json"
    _touch_sessions(sessions_dir, names)
    return (
        SyncConfig(
            account_store_path=str(sqlite_path),
            sessions_dir=str(sessions_dir),
            parser_store_path=str(parser_path),
        ),
        names,
    )


async def _fetch_account(session_name: str):
    async with db.acquire() as conn:
        return await conn.fetchrow(
            "SELECT session_name, status, is_enabled, current_task_id FROM accounts "
            "WHERE session_name = $1",
            session_name,
        )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_idempotent_two_runs(a10_cleanup, tmp_path) -> None:
    config, names = _make_config(tmp_path, "idem")
    _init_sqlite(Path(config.account_store_path), [(names[0], 0)])
    _write_parser_jobs(Path(config.parser_store_path), [names[0]])

    r1 = await sync_accounts_to_pg(config)
    r2 = await sync_accounts_to_pg(config)

    assert r1.inserted >= 3
    assert r2.unchanged >= 3
    assert r2.inserted == 0
    assert r2.updated == 0

    row = await _fetch_account(names[0])
    assert row is not None
    assert row["status"] == "active"


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_blocked_forces_disabled_even_in_clump(a10_cleanup, tmp_path) -> None:
    config, names = _make_config(tmp_path, "blocked")
    _init_sqlite(Path(config.account_store_path), [(names[0], 1)])
    _write_parser_jobs(Path(config.parser_store_path), [names[0]])

    await sync_accounts_to_pg(config)

    row = await _fetch_account(names[0])
    assert row is not None
    assert row["status"] == "disabled"
    assert row["is_enabled"] is False


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_disk_only_not_in_clump_is_disabled_and_not_pickable(
    a10_cleanup, tmp_path
) -> None:
    config, names = _make_config(tmp_path, "inactive")
    _init_sqlite(Path(config.account_store_path), [])
    _write_parser_jobs(Path(config.parser_store_path), [])

    await sync_accounts_to_pg(config)

    row = await _fetch_account(names[0])
    assert row is not None
    assert row["status"] == "disabled"
    assert row["is_enabled"] is False

    async with db.acquire() as conn:
        eligible = await conn.fetchval(
            """
            SELECT 1 FROM accounts
            WHERE session_name = $1
              AND status = 'active'
              AND is_enabled = true
              AND current_task_id IS NULL
            """,
            names[0],
        )
    assert eligible is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_in_clump_becomes_active_and_pickable(a10_cleanup, tmp_path) -> None:
    config, names = _make_config(tmp_path, "active")
    _init_sqlite(Path(config.account_store_path), [(names[0], 0)])
    _write_parser_jobs(Path(config.parser_store_path), [names[0]])

    await sync_accounts_to_pg(config)

    row = await _fetch_account(names[0])
    assert row is not None
    assert row["status"] == "active"
    assert row["is_enabled"] is True

    async with db.acquire() as conn:
        eligible = await conn.fetchval(
            """
            SELECT 1 FROM accounts
            WHERE session_name = $1
              AND status = 'active'
              AND is_enabled = true
              AND current_task_id IS NULL
              AND (cooldown_until IS NULL OR cooldown_until <= now())
            """,
            names[0],
        )
    assert eligible == 1


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_preserves_current_task_id(a10_cleanup, tmp_path) -> None:
    config, names = _make_config(tmp_path, "taskid")
    _init_sqlite(Path(config.account_store_path), [(names[0], 0)])
    _write_parser_jobs(Path(config.parser_store_path), [names[0]])

    async with db.acquire() as conn:
        task_type_id = await conn.fetchval(
            """
            INSERT INTO task_types (
                code, name, is_enabled, default_priority,
                min_available_resource_percent, max_attempts
            )
            VALUES ($1, 'test', true, 1, 0, 3)
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            f"{_PREFIX}type_{uuid.uuid4().hex[:8]}",
        )
        task_id = await conn.fetchval(
            """
            INSERT INTO task_queue (
                task_type_id, task_type_code, priority, max_attempts, dedup_key
            )
            VALUES ($1, 'test_task', 1, 3, $2)
            RETURNING id
            """,
            task_type_id,
            f"{_PREFIX}dedup_{uuid.uuid4().hex}",
        )
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled, current_task_id) "
            "VALUES ($1, 'active', true, $2) RETURNING id",
            names[0],
            task_id,
        )
    assert account_id is not None

    await sync_accounts_to_pg(config)

    row = await _fetch_account(names[0])
    assert row is not None
    assert row["current_task_id"] == task_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_preserves_cooldown_status(a10_cleanup, tmp_path) -> None:
    config, names = _make_config(tmp_path, "cooldown")
    _init_sqlite(Path(config.account_store_path), [(names[0], 0)])
    _write_parser_jobs(Path(config.parser_store_path), [names[0]])

    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'cooldown', true)",
            names[0],
        )

    await sync_accounts_to_pg(config)

    row = await _fetch_account(names[0])
    assert row is not None
    assert row["status"] == "cooldown"
