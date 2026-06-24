"""Кросс-блочные интеграционные тесты: B1 (db) + B2 (task_types) + B3 (enqueue).

Проверяют, что блоки работают вместе на живой PG:
- пул и транзакции из db.py обслуживают репозитории;
- настройки типа задачи (B2) реально попадают в строку очереди (B3);
- per-op состав (task_type_ops) согласован с тем, что enqueue кладёт в task_queue;
- dedup из B3 виден при чтении тем же пулом;
- транзакция из B1 откатывает вставку в task_queue.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_DEDUP_PREFIX = "test_int_"


def _unique_key() -> str:
    return f"{_DEDUP_PREFIX}{uuid.uuid4().hex}"


async def _cleanup_all() -> None:
    await cleanup_queue_test_data(
        dedup_key_like=f"{_DEDUP_PREFIX}%",
        session_name_like=f"{_DEDUP_PREFIX}%",
    )


@pytest.fixture
async def clean_queue(pg_pool):
    """Чистит тестовые строки (task_queue, accounts, usage) до и после теста."""
    await _cleanup_all()
    yield
    await _cleanup_all()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_chain_healthcheck_read_enqueue(clean_queue) -> None:
    """B1 → B2 → B3: пул жив, тип читается, задача создаётся из этого типа."""
    assert await db.healthcheck() is True

    task_types = TaskTypesRepo()
    queue = TaskQueueRepo(task_types=task_types)

    task_type = await task_types.get_by_code("parser_add_channel")
    assert task_type is not None and task_type.is_enabled

    result = await queue.enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            payload={"channel_ref": "@chain"},
            dedup_key=_unique_key(),
        )
    )
    assert result.created is True

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT task_type_id, task_type_code FROM task_queue WHERE id = $1",
            result.task_id,
        )
    assert row["task_type_id"] == task_type.id
    assert row["task_type_code"] == task_type.code


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_enqueued_row_matches_task_type_settings(clean_queue) -> None:
    """B2 определяет priority/max_attempts → B3 переносит их в task_queue."""
    task_types = TaskTypesRepo()
    queue = TaskQueueRepo(task_types=task_types)

    task_type = await task_types.get_by_code("parser_add_channel")
    assert task_type is not None

    result = await queue.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=_unique_key())
    )

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT priority, max_attempts FROM task_queue WHERE id = $1",
            result.task_id,
        )
    assert row["priority"] == task_type.default_priority
    assert row["max_attempts"] == task_type.max_attempts


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_explicit_priority_overrides_task_type_default(clean_queue) -> None:
    """B3 уважает явный priority поверх default из B2."""
    queue = TaskQueueRepo()
    result = await queue.enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=_unique_key(),
            priority=777,
        )
    )
    async with db.acquire() as conn:
        priority = await conn.fetchval(
            "SELECT priority FROM task_queue WHERE id = $1", result.task_id
        )
    assert priority == 777


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dual_account_type_has_per_op_roles(clean_queue) -> None:
    """B2: move_channel несёт source/target op'ы — основа для C4/C5/D5."""
    task_types = TaskTypesRepo()
    move = await task_types.get_by_code("move_channel")
    assert move is not None
    assert move.uses_two_accounts is True

    roles = {op.account_role for op in move.ops}
    assert "source" in roles and "target" in roles
    # У каждого op положительный расход и валидный rph-лимит из resource_op_types.
    assert all(op.units_per_execution >= 1 for op in move.ops)
    assert all(op.rph_limit > 0 for op in move.ops)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dedup_visible_across_pool_reads(clean_queue) -> None:
    """B3 создал задачу — она видна активным чтением через тот же пул (B1)."""
    queue = TaskQueueRepo()
    key = _unique_key()

    first = await queue.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )
    second = await queue.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )

    assert second.created is False
    assert second.existing_task_id == first.task_id

    async with db.acquire() as conn:
        active = await conn.fetchval(
            """
            SELECT COUNT(*) FROM task_queue
            WHERE dedup_key = $1
              AND status IN ('queued', 'scheduled', 'retry', 'in_progress')
            """,
            key,
        )
    assert active == 1


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_transaction_rollback_discards_insert(clean_queue) -> None:
    """B1 transaction(): ручной INSERT в task_queue откатывается при исключении."""
    task_types = TaskTypesRepo()
    task_type = await task_types.get_by_code("parser_add_channel")
    assert task_type is not None
    key = _unique_key()

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        async with db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO task_queue
                    (task_type_id, task_type_code, status, priority,
                     payload, dedup_key, max_attempts)
                VALUES ($1, $2, 'queued', $3, $4::jsonb, $5, $6)
                """,
                task_type.id,
                task_type.code,
                task_type.default_priority,
                json.dumps({"x": 1}),
                key,
                task_type.max_attempts,
            )
            raise _Boom()

    async with db.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM task_queue WHERE dedup_key = $1", key
        )
    assert count == 0


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_enqueue_then_complete_then_reuse_dedup(clean_queue) -> None:
    """Жизненный цикл dedup: active → done освобождает ключ (готовит почву для B4/B5)."""
    queue = TaskQueueRepo()
    key = _unique_key()

    first = await queue.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )
    blocked = await queue.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )
    assert blocked.created is False

    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE task_queue SET status = 'done', finished_at = now() WHERE id = $1",
            first.task_id,
        )

    reused = await queue.enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=key)
    )
    assert reused.created is True
    assert reused.task_id != first.task_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_dispatch_pipeline_b2_b3_b4_b6_b8(clean_queue) -> None:
    """Сквозной путь воркера: тип (B2) → задача (B3) → захват claim_next (B4) →
    резерв аккаунта (B6) → списание ресурса по каждому op (B8).
    Проверяет согласованность блоков.
    """
    task_types = TaskTypesRepo()
    queue = TaskQueueRepo(task_types=task_types)
    accounts = AccountsRepo()
    usage = ResourceUsageRepo()

    # B2: тип задачи с per-op составом.
    task_type = await task_types.get_by_code("parser_add_channel")
    assert task_type is not None and task_type.ops

    # B3: создаём задачу с максимальным приоритетом, чтобы claim_next забрал
    # именно её (а не чужие parser_add_channel на dev-базе).
    dedup = _unique_key()
    enqueued = await queue.enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=dedup,
            priority=2_000_000_000,
        )
    )
    assert enqueued.created is True

    # B4: воркер захватывает задачу (max priority + random среди равных).
    claimed = await queue.claim_next(
        locked_by="int-worker", task_type_codes=["parser_add_channel"]
    )
    assert claimed is not None
    assert claimed.id == enqueued.task_id
    assert claimed.attempt_count == 0
    task_id = claimed.id

    # Создаём свободный тестовый аккаунт.
    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            f"{_DEDUP_PREFIX}{uuid.uuid4().hex}",
        )

    # B6: резервируем аккаунт под захваченную задачу.
    assert await accounts.reserve(account_id, task_id) is True

    # D5: списываем ресурс per-op до execute (ТЗ §7.3).
    expected: dict[int, int] = {}
    for op in task_type.ops:
        if op.account_role != "primary" or not op.op_is_enabled:
            continue
        expected[op.op_type_id] = (
            expected.get(op.op_type_id, 0) + op.units_per_execution
        )
    await usage.record_for_task(
        task_type=task_type,
        task_id=task_id,
        accounts_by_role={"primary": account_id},
    )

    # Проверяем, что учёт за час совпал с заявленным расходом типа.
    for op_type_id, units in expected.items():
        counted = await usage.count_last_hour(account_id, op_type_id)
        assert counted == units

    # Аккаунт занят именно этой задачей.
    async with db.acquire() as conn:
        current = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", account_id
        )
    assert current == task_id

    # Освобождаем — воркер так делает после execute.
    await accounts.release(account_id)
    async with db.acquire() as conn:
        current = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", account_id
        )
    assert current is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_b4_claim_respects_priority_over_older_lower(clean_queue) -> None:
    """B3+B4: новая задача с высоким priority забирается раньше старой с низким."""
    queue = TaskQueueRepo()
    test_prio = 2_000_000_000

    old_low = await queue.enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=_unique_key(),
            priority=test_prio - 100,
        )
    )
    new_high = await queue.enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=_unique_key(),
            priority=test_prio,
        )
    )

    claimed = await queue.claim_next(
        locked_by="int-worker", task_type_codes=["parser_add_channel"]
    )
    assert claimed is not None
    assert claimed.id == new_high.task_id
    assert claimed.priority == test_prio

    await queue.complete(claimed.id)
    second = await queue.claim_next(
        locked_by="int-worker", task_type_codes=["parser_add_channel"]
    )
    assert second is not None
    assert second.id == old_low.task_id


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_b5_postpone_in_dispatch_pipeline(clean_queue) -> None:
    """B3 → B4 claim → B5 postpone: очередь не блокируется, вторая задача claim'ится."""
    queue = TaskQueueRepo()
    test_prio = 2_000_000_000

    first = await queue.enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=_unique_key(),
            priority=test_prio,
        )
    )
    second = await queue.enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=_unique_key(),
            priority=test_prio,
        )
    )
    first_id = first.task_id
    second_id = second.task_id

    claimed = await queue.claim_next(
        locked_by="int-worker", task_type_codes=["parser_add_channel"]
    )
    assert claimed is not None
    assert claimed.id in (first_id, second_id)

    await queue.postpone(claimed.id, delay_seconds=3600, reason="нет аккаунта")

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, postpone_count FROM task_queue WHERE id = $1",
            claimed.id,
        )
    assert row["status"] == "scheduled"
    assert row["postpone_count"] == 1

    other = await queue.claim_next(
        locked_by="int-worker-2", task_type_codes=["parser_add_channel"]
    )
    assert other is not None
    assert other.id != claimed.id
    assert other.id in (first_id, second_id)
