"""E2E / интеграционные тесты критериев приёмки §30 ТЗ.

Сценарии проверяют сквозное поведение системы через:
- создание задач (enqueue);
- работу воркера (QueueWorker);
- состояние PostgreSQL (task_queue, accounts, мониторинговые VIEW).

Требуют QUEUE_DATABASE_URL (@pytest.mark.integration).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.mock_adapter import MockTaskAdapter
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.resource_check import ResourceChecker
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from app_balance.queue.watchdog import StuckTaskWatchdog
from tests.queue_integration_helpers import AlwaysOkResourceChecker, reclaim_retry_task
from tests.tz30.conftest import (
    FailingTaskAdapter,
    PREFIX,
    TASK_TYPE_ADD,
    TASK_TYPE_MOVE,
    TEST_PRIORITY,
    build_worker,
    enqueue_task,
    future_run_after,
    insert_account,
    lock_all_free_accounts_except,
    occupy_account,
    pytestmark_requires_pg,
    assert_attempts_sync_with_queue,
    run_worker_until,
    run_worker_until_task_status,
    task_attempts_for_task,
    task_row,
    task_status,
    unlock_accounts,
    unique_key,
    usage_count_for_task,
    wait_task_status,
    wait_until,
)

pytestmark = [pytest.mark.tz30, pytest.mark.e2e, pytestmark_requires_pg, pytest.mark.integration]


# --- §30.1–2: создание задачи и настройки из task_types ---


@pytest.mark.asyncio
async def test_tz30_01_task_created_in_queue_with_type_settings(tz30_clean) -> None:
    """§30.1–2: задача попадает в очередь с типом, приоритетом и max_attempts из task_types."""
    task_types = TaskTypesRepo()
    expected = await task_types.get_by_code(TASK_TYPE_ADD)
    assert expected is not None

    # priority=None — из task_types (500), не из PYTEST_TEST_PRIORITY (изоляция воркера).
    task_id = await enqueue_task(priority=None)

    row = await task_row(task_id)
    assert row["status"] == "queued"
    assert row["task_type_code"] == TASK_TYPE_ADD
    assert row["priority"] == expected.default_priority
    assert row["max_attempts"] == expected.max_attempts


# --- §30.3: приоритет ---


@pytest.mark.asyncio
async def test_tz30_03_higher_priority_claimed_first(tz30_clean) -> None:
    """§30.3, §15.2: сначала берётся задача с более высоким priority."""
    low_id = await enqueue_task(priority=TEST_PRIORITY - 100)
    high_id = await enqueue_task(priority=TEST_PRIORITY)
    repo = TaskQueueRepo()

    first = await repo.claim_next(locked_by="tz30-w", task_type_codes=[TASK_TYPE_ADD])
    second = await repo.claim_next(locked_by="tz30-w", task_type_codes=[TASK_TYPE_ADD])

    assert first is not None and second is not None
    assert first.id == high_id
    assert second.id == low_id


# --- §30.4: run_after ---


@pytest.mark.asyncio
async def test_tz30_04_future_run_after_not_claimed(tz30_clean) -> None:
    """§30.4, §15.1: задача с run_after в будущем не берётся в работу."""
    await enqueue_task(run_after=future_run_after())
    ready_id = await enqueue_task(priority=TEST_PRIORITY - 50)

    claimed = await TaskQueueRepo().claim_next(
        locked_by="tz30-w",
        task_type_codes=[TASK_TYPE_ADD],
    )

    assert claimed is not None
    assert claimed.id == ready_id


# --- §30.5: атомарность — одна задача не двум воркерам ---


@pytest.mark.asyncio
async def test_tz30_05_concurrent_claims_never_duplicate_task(tz30_clean) -> None:
    """§30.5, §14: параллельный claim не отдаёт одну задачу двум процессам."""
    task_ids = {await enqueue_task() for _ in range(5)}
    repo = TaskQueueRepo()

    results = await asyncio.gather(
        *[
            repo.claim_next(locked_by=f"tz30-w{i}", task_type_codes=[TASK_TYPE_ADD])
            for i in range(5)
        ]
    )
    claimed = [r.id for r in results if r is not None]

    assert len(claimed) == 5
    assert len(set(claimed)) == 5
    assert set(claimed) == task_ids


# --- §30.6–7: подбор свободного аккаунта ---


@pytest.mark.asyncio
async def test_tz30_06_task_without_account_gets_free_account(tz30_clean) -> None:
    """§30.6: задача без account_id получает подходящий свободный аккаунт."""
    account_id = await insert_account(suffix="free")
    locked = await lock_all_free_accounts_except({account_id})
    worker = build_worker(worker_id="tz30-06")

    try:
        task_id = await enqueue_task()
        await run_worker_until(worker, processed=1)

        row = await task_row(task_id)
        async with db.acquire() as conn:
            current = await conn.fetchval(
                "SELECT current_task_id FROM accounts WHERE id = $1", account_id
            )

        assert row["status"] == "done"
        assert row["account_id"] == account_id
        assert current is None
    finally:
        await unlock_accounts(locked)


@pytest.mark.asyncio
async def test_tz30_07_picks_second_account_when_first_depleted(tz30_clean) -> None:
    """§30.7: если один аккаунт без ресурса — балансировщик выбирает другой."""
    depleted_id = await insert_account(suffix="depleted")
    fresh_id = await insert_account(suffix="fresh")

    async with db.acquire() as conn:
        task_type_id = await conn.fetchval(
            "SELECT id FROM task_types WHERE code = $1", TASK_TYPE_ADD
        )
        op_type_id = await conn.fetchval(
            "SELECT id FROM resource_op_types WHERE code = 'get_entity'"
        )
        holder = await enqueue_task(priority=TEST_PRIORITY - 1000)

    await ResourceUsageRepo().insert(
        account_id=depleted_id,
        op_type_id=op_type_id,
        task_id=holder,
        task_type_id=task_type_id,
        units=9999,
    )

    locked = await lock_all_free_accounts_except({depleted_id, fresh_id})
    worker = build_worker(worker_id="tz30-07")

    try:
        task_id = await enqueue_task()
        await run_worker_until(worker, processed=1)

        row = await task_row(task_id)
        assert row["status"] == "done"
        assert row["account_id"] == fresh_id
    finally:
        await unlock_accounts(locked)


# --- §30.8–9: конкретный аккаунт ---


@pytest.mark.asyncio
async def test_tz30_08_fixed_account_task_runs_only_on_it(tz30_clean) -> None:
    """§30.8: задача с account_id выполняется только на указанном аккаунте."""
    account_id = await insert_account(suffix="fixed")
    locked = await lock_all_free_accounts_except({account_id})
    worker = build_worker(worker_id="tz30-08")

    try:
        task_id = await enqueue_task(account_id=account_id)
        await run_worker_until(worker, processed=1)

        row = await task_row(task_id)
        assert row["status"] == "done"
        assert row["account_id"] == account_id
    finally:
        await unlock_accounts(locked)


@pytest.mark.asyncio
async def test_tz30_09_busy_fixed_account_postponed_queue_continues(tz30_clean) -> None:
    """§30.9, §15.3: занятый фиксированный аккаунт → postpone; следующая задача выполняется."""
    busy_id = await insert_account(suffix="busy")
    await occupy_account(busy_id)
    free_id = await insert_account(suffix="free")

    blocked_id = await enqueue_task(
        priority=TEST_PRIORITY + 1,
        account_id=busy_id,
    )
    pass_id = await enqueue_task(priority=TEST_PRIORITY)

    worker = build_worker(worker_id="tz30-09")
    await run_worker_until(worker, processed=1)

    blocked = await task_row(blocked_id)
    passed = await task_row(pass_id)

    assert blocked["status"] == "scheduled"
    assert blocked["postpone_count"] == 1
    assert "account_reserve_failed" in (blocked["last_error"] or "")
    assert passed["status"] == "done"
    assert passed["account_id"] == free_id
    assert await usage_count_for_task(blocked_id) == 0


# --- §30.10–11: учёт попыток ---


@pytest.mark.asyncio
async def test_tz30_10_successful_attempt_increments_attempt_count(tz30_clean) -> None:
    """§30.10: реальная успешная попытка увеличивает attempt_count."""
    account_id = await insert_account(suffix="success")
    locked = await lock_all_free_accounts_except({account_id})
    worker = build_worker(worker_id="tz30-10")

    try:
        task_id = await enqueue_task(account_id=account_id)
        await run_worker_until(worker, processed=1)

        row = await task_row(task_id)
        assert row["status"] == "done"
        assert int(row["attempt_count"]) >= 1
        attempts = await task_attempts_for_task(task_id)
        assert len(attempts) == int(row["attempt_count"])
        assert attempts[0]["status"] == "success"
        assert attempts[0]["attempt_number"] == 1
        await assert_attempts_sync_with_queue(task_id)
    finally:
        await unlock_accounts(locked)


@pytest.mark.asyncio
async def test_tz30_11_failed_attempt_counts_and_goes_to_retry(tz30_clean) -> None:
    """§30.11, §30.18: неуспешная попытка учитывается (attempt_count) и даёт retry."""
    account_id = await insert_account(suffix="fail")
    locked = await lock_all_free_accounts_except({account_id})
    worker = build_worker(
        worker_id="tz30-11",
        adapter=FailingTaskAdapter("flood_wait"),
    )

    try:
        task_id = await enqueue_task(account_id=account_id)
        # processed не растёт при retry — ждём status=retry, не worker.processed.
        await run_worker_until_task_status(worker, task_id, "retry")

        row = await task_row(task_id)
        assert row["status"] == "retry"
        assert int(row["attempt_count"]) >= 1
        assert row["run_after"] is not None
        assert row["last_error"] == "flood_wait"
        attempts = await task_attempts_for_task(task_id)
        assert len(attempts) == int(row["attempt_count"])
        assert attempts[0]["status"] == "error"
        assert attempts[0]["attempt_number"] == 1
        await assert_attempts_sync_with_queue(task_id)
    finally:
        await unlock_accounts(locked)


@pytest.mark.asyncio
async def test_tz30_18_retry_run_after_uses_backoff(tz30_clean) -> None:
    """§30.18 / E3: run_after растёт по backoff task_types (10s → 20s)."""
    saved = None
    async with db.acquire() as conn:
        saved = await conn.fetchrow(
            """
            SELECT retry_delay_seconds, retry_backoff_multiplier, max_retry_delay_seconds
            FROM task_types WHERE code = $1
            """,
            TASK_TYPE_ADD,
        )
        await conn.execute(
            """
            UPDATE task_types
            SET retry_delay_seconds = 10,
                retry_backoff_multiplier = 2,
                max_retry_delay_seconds = 1800
            WHERE code = $1
            """,
            TASK_TYPE_ADD,
        )

    account_id = await insert_account(suffix="backoff")
    locked = await lock_all_free_accounts_except({account_id})
    worker = build_worker(
        worker_id="tz30-18",
        adapter=FailingTaskAdapter("transient_error"),
    )

    try:
        task_id = await enqueue_task(account_id=account_id)
        t0 = datetime.now(timezone.utc)
        await run_worker_until_task_status(worker, task_id, "retry")

        row1 = await task_row(task_id)
        assert row1["status"] == "retry"
        assert int(row1["attempt_count"]) == 1
        delta1 = (row1["run_after"] - t0).total_seconds()
        assert 8 <= delta1 <= 14, f"1-я задержка ~10s, получили {delta1}s"

        # Фаза 2: прямой dispatch по id — worker с claim_next на shared PG
        # может 10+ секунд обрабатывать чужие задачи и не трогать нашу.
        t1 = datetime.now(timezone.utc)
        claimed2 = await reclaim_retry_task(task_id, locked_by="tz30-18b")
        dispatcher2 = TaskDispatcher(
            queue=TaskQueueRepo(),
            accounts=AccountsRepo(),
            task_types=TaskTypesRepo(),
            adapter=FailingTaskAdapter("transient_error"),
            resource_check=AlwaysOkResourceChecker(),
            postpone_delay_seconds=300,
        )
        result2 = await dispatcher2.dispatch(claimed2)
        assert result2 == DispatchResult.RETRIED

        row2 = await task_row(task_id)
        assert int(row2["attempt_count"]) == 2
        delta2 = (row2["run_after"] - t1).total_seconds()
        assert 18 <= delta2 <= 24, f"2-я задержка ~20s, получили {delta2}s"
    finally:
        await unlock_accounts(locked)
        if saved is not None:
            async with db.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE task_types
                    SET retry_delay_seconds = $2,
                        retry_backoff_multiplier = $3,
                        max_retry_delay_seconds = $4
                    WHERE code = $1
                    """,
                    TASK_TYPE_ADD,
                    saved["retry_delay_seconds"],
                    saved["retry_backoff_multiplier"],
                    saved["max_retry_delay_seconds"],
                )


# --- §30.12: недостаток ресурса ---


@pytest.mark.asyncio
async def test_tz30_12_insufficient_resource_postpones_without_execute(tz30_clean) -> None:
    """§30.12: при нехватке ресурса задача откладывается, execute не вызывается."""
    account_id = await insert_account(suffix="empty")

    async with db.acquire() as conn:
        task_type_id = await conn.fetchval(
            "SELECT id FROM task_types WHERE code = $1", TASK_TYPE_ADD
        )
        op_type_id = await conn.fetchval(
            "SELECT id FROM resource_op_types WHERE code = 'get_entity'"
        )
        holder = await enqueue_task(priority=TEST_PRIORITY - 1000)

    await ResourceUsageRepo().insert(
        account_id=account_id,
        op_type_id=op_type_id,
        task_id=holder,
        task_type_id=task_type_id,
        units=9999,
    )

    task_id = await enqueue_task(account_id=account_id)
    worker = build_worker(worker_id="tz30-12")
    run_task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(wait_task_status(task_id, "scheduled"), timeout=5.0)
    finally:
        worker.stop()
        await asyncio.wait_for(run_task, timeout=5.0)

    row = await task_row(task_id)
    assert row["postpone_count"] == 1
    assert "insufficient_resource" in (row["last_error"] or "")
    assert await usage_count_for_task(task_id) == 0
    assert await task_attempts_for_task(task_id) == []
    assert int(row["attempt_count"]) == 0


# --- §30.13–14: dual-account move_channel ---


@pytest.mark.asyncio
async def test_tz30_13_14_move_channel_requires_both_accounts(tz30_clean) -> None:
    """§30.13–14, §18: перенос канала резервирует source и target, завершается done."""
    source_id = await insert_account(suffix="src")
    target_id = await insert_account(suffix="tgt")

    task_id = await enqueue_task(
        task_type_code=TASK_TYPE_MOVE,
        source_account_id=source_id,
        target_account_id=target_id,
    )

    repo = TaskQueueRepo()
    claimed = await repo.claim_next(
        locked_by="tz30-move",
        task_type_codes=[TASK_TYPE_MOVE],
    )
    assert claimed is not None and claimed.id == task_id

    dispatcher = TaskDispatcher(
        queue=repo,
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=MockTaskAdapter(),
        resource_check=ResourceChecker(ResourceUsageRepo()),
        postpone_delay_seconds=300,
    )
    result = await dispatcher.dispatch(claimed)

    assert result == DispatchResult.COMPLETED

    async with db.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", task_id
        )
        source_busy = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", source_id
        )
        target_busy = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", target_id
        )

    assert status == "done"
    assert source_busy is None
    assert target_busy is None


@pytest.mark.asyncio
async def test_tz30_13_move_postponed_when_target_resource_low(tz30_clean) -> None:
    """§30.13: если у target не хватает ресурса — перенос не запускается."""
    source_id = await insert_account(suffix="src_ok")
    target_id = await insert_account(suffix="tgt_low")

    async with db.acquire() as conn:
        task_type_id = await conn.fetchval(
            "SELECT id FROM task_types WHERE code = $1", TASK_TYPE_MOVE
        )
        op_rows = await conn.fetch(
            """
            SELECT tto.account_role, rot.id AS op_type_id
            FROM task_type_ops tto
            JOIN resource_op_types rot ON rot.id = tto.op_type_id
            WHERE tto.task_type_id = $1
            """,
            task_type_id,
        )
        holder = await enqueue_task(priority=TEST_PRIORITY - 1000)

    target_op_id = next(
        int(r["op_type_id"]) for r in op_rows if r["account_role"] == "target"
    )
    await ResourceUsageRepo().insert(
        account_id=target_id,
        op_type_id=target_op_id,
        task_id=holder,
        task_type_id=task_type_id,
        units=9999,
    )

    task_id = await enqueue_task(
        task_type_code=TASK_TYPE_MOVE,
        source_account_id=source_id,
        target_account_id=target_id,
    )

    repo = TaskQueueRepo()
    claimed = await repo.claim_next(
        locked_by="tz30-move-postpone",
        task_type_codes=[TASK_TYPE_MOVE],
    )
    assert claimed is not None

    dispatcher = TaskDispatcher(
        queue=repo,
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=MockTaskAdapter(),
        resource_check=ResourceChecker(ResourceUsageRepo()),
        postpone_delay_seconds=300,
    )
    result = await dispatcher.dispatch(claimed)

    assert result == DispatchResult.POSTPONED
    row = await task_row(task_id)
    assert row["status"] == "scheduled"
    assert row["postpone_count"] == 1
    assert "insufficient_resource" in (row["last_error"] or "")


# --- §30.15: dedup ---


@pytest.mark.asyncio
async def test_tz30_15_active_dedup_prevents_duplicate_tasks(tz30_clean) -> None:
    """§30.15, §12: повторный enqueue с тем же dedup_key не создаёт вторую активную задачу."""
    key = unique_key()
    repo = TaskQueueRepo()

    first = await repo.enqueue(
        EnqueueInput(task_type_code=TASK_TYPE_ADD, dedup_key=key)
    )
    second = await repo.enqueue(
        EnqueueInput(task_type_code=TASK_TYPE_ADD, dedup_key=key)
    )

    assert first.created is True
    assert second.created is False
    assert second.existing_task_id == first.task_id

    async with db.acquire() as conn:
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM task_queue
            WHERE dedup_key = $1
              AND status IN ('queued', 'scheduled', 'retry', 'in_progress')
            """,
            key,
        )
    assert count == 1


@pytest.mark.asyncio
async def test_tz30_15_dedup_allows_new_task_after_done(tz30_clean) -> None:
    """§12: после done по dedup_key можно создать новую задачу."""
    key = unique_key()
    repo = TaskQueueRepo()

    first = await repo.enqueue(
        EnqueueInput(task_type_code=TASK_TYPE_ADD, dedup_key=key)
    )
    assert first.created and first.task_id

    claimed = await repo.claim_next(locked_by="tz30-dedup", task_type_codes=[TASK_TYPE_ADD])
    assert claimed is not None
    await repo.complete(first.task_id)

    second = await repo.enqueue(
        EnqueueInput(task_type_code=TASK_TYPE_ADD, dedup_key=key)
    )
    assert second.created is True
    assert second.task_id != first.task_id


# --- §30.16–17: настройки типа задачи в БД ---


@pytest.mark.asyncio
async def test_tz30_16_17_task_type_limits_stored_in_database(tz30_clean) -> None:
    """§30.16–17: target_queue_size и min_available_resource_percent задаются в task_types."""
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT code, target_queue_size, min_available_resource_percent, max_attempts
            FROM task_types
            WHERE code IN ($1, $2)
            ORDER BY code
            """,
            TASK_TYPE_ADD,
            TASK_TYPE_MOVE,
        )

    assert row is not None
    # Хотя бы один MVP-тип имеет управляемые лимиты (не захардкожены в коде).
    async with db.acquire() as conn:
        configured = await conn.fetchval(
            """
            SELECT COUNT(*) FROM task_types
            WHERE is_enabled = true
              AND min_available_resource_percent IS NOT NULL
              AND max_attempts IS NOT NULL
            """
        )
    assert int(configured) >= 1


# --- §30.18: retry через run_after ---


@pytest.mark.asyncio
async def test_tz30_18_retry_task_claimed_after_run_after(tz30_clean) -> None:
    """§30.18, §20: задача в retry с наступившим run_after снова берётся в работу."""
    task_id = await enqueue_task()
    repo = TaskQueueRepo()

    claimed = await repo.claim_next(locked_by="tz30-retry", task_type_codes=[TASK_TYPE_ADD])
    assert claimed is not None
    await repo.begin_execution_attempt(task_id)
    status = await repo.reschedule_or_fail(task_id, "temporary", retry_delay_seconds=0)
    assert status == "retry"

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET run_after = now() - interval '1 second' WHERE id = $1",
            task_id,
        )

    reclaimed = await repo.claim_next(locked_by="tz30-retry-2", task_type_codes=[TASK_TYPE_ADD])
    assert reclaimed is not None
    assert reclaimed.id == task_id
    assert reclaimed.attempt_count == 1


# --- §30.19: зависание in_progress ---


@pytest.mark.asyncio
async def test_tz30_19_stuck_in_progress_becomes_stuck(tz30_clean) -> None:
    """§30.19, §13.4: задача дольше task_timeout_seconds → stuck, аккаунт освобождён."""
    account_id = await insert_account(suffix="stuck")
    task_id = await enqueue_task(account_id=account_id)
    repo = TaskQueueRepo()

    claimed = await repo.claim_next(locked_by="tz30-stuck", task_type_codes=[TASK_TYPE_ADD])
    assert claimed is not None

    async with db.acquire() as conn:
        await conn.execute(
            """
            UPDATE task_types
            SET task_timeout_seconds = 5
            WHERE code = $1
            """,
            TASK_TYPE_ADD,
        )
        await conn.execute(
            """
            UPDATE task_queue
            SET locked_at = now() - interval '10 seconds',
                started_at = now() - interval '1 hour',
                locked_until = now() + interval '1 hour'
            WHERE id = $1
            """,
            task_id,
        )
        await conn.execute(
            "UPDATE accounts SET current_task_id = $2 WHERE id = $1",
            account_id,
            task_id,
        )

    stuck = await repo.mark_stuck_timed_out(limit=10)
    assert any(s.id == task_id for s in stuck)

    row = await task_row(task_id)
    async with db.acquire() as conn:
        current = await conn.fetchval(
            "SELECT current_task_id FROM accounts WHERE id = $1", account_id
        )

    assert row["status"] == "stuck"
    assert current is None


# --- §30.20: мониторинг ---


@pytest.mark.asyncio
async def test_tz30_20_monitoring_views_reflect_queue_state(tz30_clean) -> None:
    """§30.20, §26: мониторинговые VIEW видят stuck и частые postpone."""
    stuck_key = f"{PREFIX}stuck_{uuid.uuid4().hex}"
    high_postpone_key = f"{PREFIX}hp_{uuid.uuid4().hex}"

    async with db.acquire() as conn:
        stuck_id = await conn.fetchval(
            """
            INSERT INTO task_queue (
                task_type_id, task_type_code, status, priority, dedup_key,
                max_attempts, started_at, locked_by
            )
            SELECT id, code, 'stuck', 1, $2, max_attempts, now() - interval '2 hours', 'ghost'
            FROM task_types WHERE code = $1
            RETURNING id
            """,
            TASK_TYPE_ADD,
            stuck_key,
        )
        postpone_id = await conn.fetchval(
            """
            INSERT INTO task_queue (
                task_type_id, task_type_code, status, priority, dedup_key,
                max_attempts, postpone_count
            )
            SELECT id, code, 'scheduled', 1, $2, max_attempts, 999
            FROM task_types WHERE code = $1
            RETURNING id
            """,
            TASK_TYPE_ADD,
            high_postpone_key,
        )

        metrics = await conn.fetchrow("SELECT * FROM v_queue_metrics")
        high_postpone = await conn.fetch(
            "SELECT id FROM v_high_postpone_tasks WHERE id = $1",
            postpone_id,
        )

    assert int(metrics["stuck_tasks_count"]) >= 1
    assert len(high_postpone) == 1

    async with db.acquire() as conn:
        await conn.execute(
            "DELETE FROM task_queue WHERE id = ANY($1::bigint[])",
            [stuck_id, postpone_id],
        )


@pytest.mark.asyncio
async def test_tz30_20b_monitoring_views_reflect_accounts_and_resource(
    tz30_clean,
) -> None:
    """§30.20, §26.3 (G2): VIEW видят cooldown и исчерпание ресурса per-op."""
    cooldown_account_id = await insert_account(suffix="cooldown")
    exhausted_account_id = await insert_account(suffix="exhausted")
    task_id = await enqueue_task(account_id=exhausted_account_id)

    async with db.acquire() as conn:
        cooldown_session = await conn.fetchval(
            "SELECT session_name FROM accounts WHERE id = $1", cooldown_account_id
        )
        task_type_id = await conn.fetchval(
            "SELECT id FROM task_types WHERE code = $1", TASK_TYPE_ADD
        )
        op = await conn.fetchrow(
            "SELECT id, rph_limit, reserve_percent FROM resource_op_types "
            "WHERE code = 'get_entity'"
        )
        cooldown_before = await conn.fetchval(
            "SELECT accounts_in_cooldown FROM v_accounts_overview"
        )

    effective_rph = int(op["rph_limit"] * (1 - float(op["reserve_percent"]) / 100.0))
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    assert await AccountsRepo().set_cooldown(cooldown_session, until) is True

    await ResourceUsageRepo().insert(
        account_id=exhausted_account_id,
        op_type_id=op["id"],
        task_id=task_id,
        task_type_id=task_type_id,
        units=effective_rph,
    )

    async with db.acquire() as conn:
        cooldown_after = await conn.fetchval(
            "SELECT accounts_in_cooldown FROM v_accounts_overview"
        )
        summary = await conn.fetchrow(
            "SELECT any_op_exhausted, worst_available_percent "
            "FROM v_account_resource_summary WHERE account_id = $1",
            exhausted_account_id,
        )

    assert int(cooldown_after) == int(cooldown_before) + 1
    assert summary is not None
    assert summary["any_op_exhausted"] is True
    assert float(summary["worst_available_percent"]) == 0.0


# --- Сквозной E2E: полный жизненный цикл §13.1 ---


@pytest.mark.asyncio
async def test_tz30_e2e_happy_path_enqueue_worker_done(tz30_clean) -> None:
    """§13.1, §30: инструмент создаёт задачу → воркер выполняет → done, аккаунт свободен."""
    account_id = await insert_account(suffix="e2e")
    locked = await lock_all_free_accounts_except({account_id})
    mock = MockTaskAdapter()
    worker = build_worker(worker_id="tz30-e2e", adapter=mock)

    try:
        task_id = await enqueue_task(
            payload={"ref": "@tz30_channel"},
            account_id=None,
        )
        await run_worker_until(worker, processed=1)

        row = await task_row(task_id)
        async with db.acquire() as conn:
            current = await conn.fetchval(
                "SELECT current_task_id FROM accounts WHERE id = $1", account_id
            )

        assert row["status"] == "done"
        assert row["account_id"] == account_id
        assert row["finished_at"] is not None
        assert current is None
        executions = [e for e in mock.executions if e.task_id == task_id]
        assert len(executions) == 1
    finally:
        await unlock_accounts(locked)
