"""B6 — интеграционные тесты AccountsRepo (pick / reserve / release)."""
from __future__ import annotations

import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_b6_"
# Ниже prod-приоритета и PYTEST_TEST_PRIORITY — holder не мешает claim.
_HOLDER_PRIORITY = -2_000_000_000


async def _enqueue_holder() -> int:
    res = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=f"{_PREFIX}holder_{uuid.uuid4().hex}",
            priority=_HOLDER_PRIORITY,
        )
    )
    assert res.created and res.task_id is not None
    return int(res.task_id)


async def _occupy_account(account_id: int) -> int:
    holder_task_id = await _enqueue_holder()
    async with db.acquire() as conn:
        reserved = await conn.fetchval(
            """
            UPDATE accounts
            SET current_task_id = $2, last_used_at = now()
            WHERE id = $1 AND current_task_id IS NULL
            RETURNING id
            """,
            account_id,
            holder_task_id,
        )
    assert reserved is not None
    return holder_task_id


async def _lock_all_free_accounts_except(exclude_ids: set[int]) -> list[tuple[int, int]]:
    """Занимает все свободные аккаунты в БД, кроме указанных (изоляция на shared dev PG)."""
    locked: list[tuple[int, int]] = []
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id FROM accounts
            WHERE status IN ('active', 'cooldown')
              AND is_enabled = true
              AND current_task_id IS NULL
              AND (cooldown_until IS NULL OR cooldown_until <= now())
            """
        )
    for row in rows:
        account_id = int(row["id"])
        if account_id in exclude_ids:
            continue
        holder_task_id = await _occupy_account(account_id)
        locked.append((account_id, holder_task_id))
    return locked


async def _unlock_accounts(locked: list[tuple[int, int]]) -> None:
    for account_id, holder_task_id in locked:
        async with db.acquire() as conn:
            await conn.execute(
                """
                UPDATE accounts
                SET current_task_id = NULL
                WHERE id = $1 AND current_task_id = $2
                """,
                account_id,
                holder_task_id,
            )
            await conn.execute(
                """
                DELETE FROM task_queue
                WHERE id = $1 AND dedup_key LIKE $2
                """,
                holder_task_id,
                f"{_PREFIX}%",
            )


async def _cleanup_b6() -> None:
    """Безопасная очистка test_b6_* с учётом FK accounts ↔ task_queue на shared dev PG."""
    await cleanup_queue_test_data(
        dedup_key_like=f"{_PREFIX}%",
        session_name_like=f"{_PREFIX}%",
    )


@pytest.fixture
async def account_and_task(pg_pool):
    """Создаёт тестовый аккаунт и задачу (для FK current_task_id). Чистит за собой."""
    session_name = f"{_PREFIX}{uuid.uuid4().hex}"
    dedup_key = f"{_PREFIX}{uuid.uuid4().hex}"

    async def _cleanup() -> None:
        await _cleanup_b6()

    await _cleanup()

    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )

    enqueue = await TaskQueueRepo().enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=dedup_key)
    )

    yield account_id, enqueue.task_id
    await _cleanup()


async def _current_task(account_id: int) -> int | None:
    async with db.acquire() as conn:
        return await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", account_id
        )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_reserve_marks_account_busy(account_and_task) -> None:
    account_id, task_id = account_and_task
    repo = AccountsRepo()

    assert await repo.reserve(account_id, task_id) is True
    assert await _current_task(account_id) == task_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_reserve_is_atomic_second_fails(account_and_task) -> None:
    account_id, task_id = account_and_task
    repo = AccountsRepo()

    assert await repo.reserve(account_id, task_id) is True
    # Повторный резерв занятого аккаунта не проходит.
    assert await repo.reserve(account_id, task_id) is False


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_release_frees_account(account_and_task) -> None:
    account_id, task_id = account_and_task
    repo = AccountsRepo()

    await repo.reserve(account_id, task_id)
    await repo.release(account_id)

    assert await _current_task(account_id) is None
    # После release снова можно зарезервировать.
    assert await repo.reserve(account_id, task_id) is True


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_reserved_account_not_pickable(account_and_task) -> None:
    account_id, task_id = account_and_task
    repo = AccountsRepo()
    await repo.reserve(account_id, task_id)

    # Занятый аккаунт исключён из выборки свободных (тот же фильтр, что в pick()).
    async with db.acquire() as conn:
        pickable = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM accounts
                WHERE id = $1
                  AND status IN ('active', 'cooldown') AND is_enabled = true
                  AND current_task_id IS NULL
                  AND (cooldown_until IS NULL OR cooldown_until <= now())
            )
            """,
            account_id,
        )
    assert pickable is False


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_pick_returns_free_account(account_and_task) -> None:
    # Есть хотя бы один свободный активный аккаунт (только что созданный).
    picked = await AccountsRepo().pick()
    assert picked is not None
    assert picked.current_task_id is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_pick_and_reserve_sets_current_task(account_and_task) -> None:
    account_id, task_id = account_and_task
    repo = AccountsRepo()

    picked = await repo.pick_and_reserve(task_id)
    assert picked is not None
    assert picked.id == account_id
    assert await _current_task(account_id) == task_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_pick_and_reserve_skips_excluded_account(pg_pool) -> None:
    """C5: exclude_account_ids — не возвращать уже отвергнутые аккаунты."""
    suffix = uuid.uuid4().hex
    s1 = f"{_PREFIX}a_{suffix}"
    s2 = f"{_PREFIX}b_{suffix}"

    await _cleanup_b6()
    async with db.acquire() as conn:
        id1 = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            s1,
        )
        id2 = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            s2,
        )

    repo = AccountsRepo()
    holder_task = await _enqueue_holder()

    locked = await _lock_all_free_accounts_except({id1, id2})
    try:
        first = await repo.pick_and_reserve(
            holder_task, exclude_account_ids=frozenset({id1})
        )
        assert first is not None
        assert first.id == id2
        await repo.release(id2)

        only_excluded = await repo.pick_and_reserve(
            holder_task, exclude_account_ids=frozenset({id1, id2})
        )
        assert only_excluded is None
    finally:
        await _unlock_accounts(locked)

    await _cleanup_b6()
