"""G0 — верификация мониторинговых VIEW блока G (G1/G2, §26.2/26.3).

Формально закрывает G1 (VIEW очереди) и G2 (VIEW ресурсов/cooldown/error rate):
smoke-проверка исполнимости всех VIEW и поведенческие тесты на shared PG.
SQL-слой реализован в DB/BD_schema.sql и DB/A8_integrate_main_db.sql.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg, TEST_ISOLATION_PRIORITY
from tests.pg_cleanup import cleanup_queue_test_data

pytestmark = [requires_pg, pytest.mark.integration]

_PREFIX = "test_g0_"
_TASK_TYPE_ADD = "parser_add_channel"
_OP_GET_ENTITY = "get_entity"

# Все мониторинговые VIEW блока G (зеркало MONITORING_VIEWS в preflight).
_MONITORING_VIEWS: tuple[str, ...] = (
    "v_queue_size_by_status",
    "v_queue_size_by_type",
    "v_queue_metrics",
    "v_high_postpone_tasks",
    "v_account_op_usage_last_hour",
    "v_account_resource_summary",
    "v_accounts_overview",
    "v_account_error_rate_last_hour",
    "v_task_type_error_rate_last_hour",
    "v_channel_capacity_usage",
    "v_recurring_errors_window",
)


async def _cleanup() -> None:
    await cleanup_queue_test_data(
        dedup_key_like=f"{_PREFIX}%",
        session_name_like=f"{_PREFIX}%",
    )


@pytest.fixture
async def g0_clean(pg_pool):
    await _cleanup()
    yield
    await _cleanup()


@pytest.fixture
async def queue_ctx(g0_clean):
    """Аккаунт + задача + op_type_id + task_type_id. Чистит за собой через g0_clean."""
    session_name = f"{_PREFIX}{uuid.uuid4().hex}"
    dedup_key = f"{_PREFIX}{uuid.uuid4().hex}"

    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )
        task_type_id = await conn.fetchval(
            "SELECT id FROM task_types WHERE code = $1", _TASK_TYPE_ADD
        )
        op = await conn.fetchrow(
            "SELECT id, rph_limit, reserve_percent FROM resource_op_types "
            "WHERE code = $1",
            _OP_GET_ENTITY,
        )

    enqueue = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code=_TASK_TYPE_ADD,
            dedup_key=dedup_key,
            priority=TEST_ISOLATION_PRIORITY,
            account_id=account_id,
        )
    )

    effective_rph = int(op["rph_limit"] * (1 - float(op["reserve_percent"]) / 100.0))

    return {
        "session_name": session_name,
        "dedup_key": dedup_key,
        "account_id": account_id,
        "task_id": enqueue.task_id,
        "task_type_id": task_type_id,
        "op_type_id": op["id"],
        "effective_rph": effective_rph,
    }


# --- 2.1. Smoke / схема ------------------------------------------------------


@pytest.mark.asyncio
async def test_all_monitoring_views_queryable(pg_pool) -> None:
    """Все 10 VIEW существуют и исполняются без ошибок."""
    async with db.acquire() as conn:
        for view in _MONITORING_VIEWS:
            await conn.execute(f'SELECT * FROM "{view}" LIMIT 1')


@pytest.mark.asyncio
async def test_v_queue_metrics_has_expected_columns(pg_pool) -> None:
    """v_queue_metrics отдаёт сводные колонки контракта §26.2."""
    async with db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM v_queue_metrics")
    assert row is not None
    for col in (
        "queue_size_total",
        "queued_count",
        "scheduled_count",
        "in_progress_count",
        "retry_tasks_count",
        "stuck_tasks_count",
        "failed_tasks_count",
        "postponed_tasks_count",
        "done_tasks_last_5_min",
        "oldest_queued_task_age_seconds",
    ):
        assert col in row, f"v_queue_metrics не содержит колонку {col}"


# --- 2.2. Поведение VIEW очереди (G1) ----------------------------------------


@pytest.mark.asyncio
async def test_v_queue_size_by_status_reflects_insert(queue_ctx) -> None:
    """Задача в статусе queued отражается в v_queue_size_by_status."""
    async with db.acquire() as conn:
        count = await conn.fetchval(
            "SELECT tasks_count FROM v_queue_size_by_status WHERE status = 'queued'"
        )
    assert count is not None and int(count) >= 1


@pytest.mark.asyncio
async def test_v_queue_size_by_type_groups_active_statuses(queue_ctx) -> None:
    """Активная задача попадает в VIEW по типу; done — исключается."""
    async with db.acquire() as conn:
        active = await conn.fetchval(
            "SELECT COALESCE(SUM(tasks_count), 0) FROM v_queue_size_by_type "
            "WHERE task_type_code = $1",
            _TASK_TYPE_ADD,
        )
    assert int(active) >= 1

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET status = 'done', finished_at = now() WHERE id = $1",
            queue_ctx["task_id"],
        )
        done_in_active = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM v_queue_size_by_type "
            "WHERE task_type_code = $1 AND status = 'done')",
            _TASK_TYPE_ADD,
        )
    assert done_in_active is False


@pytest.mark.asyncio
async def test_v_queue_metrics_oldest_queued_age(queue_ctx) -> None:
    """Самая старая queued-задача даёт положительный oldest_queued_task_age_seconds."""
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET created_at = now() - interval '120 seconds' "
            "WHERE id = $1",
            queue_ctx["task_id"],
        )
        age = await conn.fetchval(
            "SELECT oldest_queued_task_age_seconds FROM v_queue_metrics"
        )
    assert int(age) >= 100


@pytest.mark.asyncio
async def test_v_queue_metrics_oldest_age_includes_scheduled(queue_ctx) -> None:
    """G1 §26.2: scheduled без queued тоже учитывается в oldest_queued_task_age_seconds."""
    async with db.acquire() as conn:
        await conn.execute(
            """
            UPDATE task_queue
            SET status = 'scheduled',
                created_at = now() - interval '180 seconds'
            WHERE id = $1
            """,
            queue_ctx["task_id"],
        )
        age = await conn.fetchval(
            "SELECT oldest_queued_task_age_seconds FROM v_queue_metrics"
        )
    assert int(age) >= 150


@pytest.mark.asyncio
async def test_v_high_postpone_tasks_lists_postponed(queue_ctx) -> None:
    """Задача с postpone_count > 0 в статусе scheduled видна в v_high_postpone_tasks."""
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET status = 'scheduled', postpone_count = 7 "
            "WHERE id = $1",
            queue_ctx["task_id"],
        )
        row = await conn.fetchrow(
            "SELECT postpone_count FROM v_high_postpone_tasks WHERE id = $1",
            queue_ctx["task_id"],
        )
    assert row is not None and int(row["postpone_count"]) == 7


# --- 2.3. Поведение VIEW ресурсов (G2) ---------------------------------------


@pytest.mark.asyncio
async def test_v_account_op_usage_has_expected_columns(pg_pool) -> None:
    """v_account_op_usage_last_hour отдаёт per-op колонки контракта §26.3."""
    expected = {
        "account_id",
        "session_name",
        "account_status",
        "op_type_id",
        "op_code",
        "rph_limit",
        "reserve_percent",
        "effective_rph",
        "used_last_hour",
        "available_resource",
        "available_resource_percent",
    }
    async with db.acquire() as conn:
        cols = {
            row["column_name"]
            for row in await conn.fetch(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'v_account_op_usage_last_hour'
                """
            )
        }
    assert expected <= cols, f"отсутствуют колонки: {expected - cols}"


@pytest.mark.asyncio
async def test_v_account_op_usage_uses_effective_rph(queue_ctx) -> None:
    """available_resource_percent считается от effective_rph (per-op §0.5), не hourly_limit."""
    repo = ResourceUsageRepo()
    before = await repo.op_availability(queue_ctx["account_id"], queue_ctx["op_type_id"])
    assert before is not None
    assert before.used_last_hour == 0
    assert before.effective_rph == queue_ctx["effective_rph"]
    assert before.available_resource == queue_ctx["effective_rph"]

    await repo.insert(
        account_id=queue_ctx["account_id"],
        op_type_id=queue_ctx["op_type_id"],
        task_id=queue_ctx["task_id"],
        task_type_id=queue_ctx["task_type_id"],
        units=1,
    )

    after = await repo.op_availability(queue_ctx["account_id"], queue_ctx["op_type_id"])
    assert after is not None
    assert after.used_last_hour == 1
    assert after.available_resource == before.available_resource - 1
    assert after.available_resource_percent < before.available_resource_percent


@pytest.mark.asyncio
async def test_v_account_resource_summary_worst_op(queue_ctx) -> None:
    """Исчерпание op → any_op_exhausted=true, worst_available_percent падает в 0."""
    repo = ResourceUsageRepo()
    await repo.insert(
        account_id=queue_ctx["account_id"],
        op_type_id=queue_ctx["op_type_id"],
        task_id=queue_ctx["task_id"],
        task_type_id=queue_ctx["task_type_id"],
        units=queue_ctx["effective_rph"],
    )

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT worst_available_percent, any_op_exhausted, exhausted_ops_count "
            "FROM v_account_resource_summary WHERE account_id = $1",
            queue_ctx["account_id"],
        )
    assert row is not None
    assert row["any_op_exhausted"] is True
    assert int(row["exhausted_ops_count"]) >= 1
    assert float(row["worst_available_percent"]) == 0.0


@pytest.mark.asyncio
async def test_v_accounts_overview_active_and_cooldown(queue_ctx) -> None:
    """Перевод тестового аккаунта в cooldown увеличивает accounts_in_cooldown."""
    async with db.acquire() as conn:
        before = await conn.fetchval(
            "SELECT accounts_in_cooldown FROM v_accounts_overview"
        )

    until = datetime.now(timezone.utc) + timedelta(hours=1)
    assert await AccountsRepo().set_cooldown(queue_ctx["session_name"], until) is True

    async with db.acquire() as conn:
        after = await conn.fetchval(
            "SELECT accounts_in_cooldown FROM v_accounts_overview"
        )
    assert int(after) == int(before) + 1


@pytest.mark.asyncio
async def test_v_accounts_overview_active_count(queue_ctx) -> None:
    """Новый active-аккаунт увеличивает active_accounts_count в v_accounts_overview."""
    session_name = f"{_PREFIX}active_{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        before = await conn.fetchval(
            "SELECT active_accounts_count FROM v_accounts_overview"
        )
        await conn.execute(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true)",
            session_name,
        )
        after = await conn.fetchval(
            "SELECT active_accounts_count FROM v_accounts_overview"
        )
        await conn.execute("DELETE FROM accounts WHERE session_name = $1", session_name)
    assert int(after) == int(before) + 1


@pytest.mark.asyncio
async def test_v_accounts_overview_without_resource(queue_ctx) -> None:
    """Аккаунт с исчерпанным op попадает в accounts_without_resource.

    accounts_without_resource = число аккаунтов, у которых исчерпан хотя бы один
    enabled op (any_op_exhausted). После исчерпания get_entity наш аккаунт
    обязан числиться без ресурса, а сводный счётчик — быть не меньше 1.
    """
    await ResourceUsageRepo().insert(
        account_id=queue_ctx["account_id"],
        op_type_id=queue_ctx["op_type_id"],
        task_id=queue_ctx["task_id"],
        task_type_id=queue_ctx["task_type_id"],
        units=queue_ctx["effective_rph"],
    )

    async with db.acquire() as conn:
        my_exhausted = await conn.fetchval(
            "SELECT any_op_exhausted FROM v_account_resource_summary "
            "WHERE account_id = $1",
            queue_ctx["account_id"],
        )
        overview_count = await conn.fetchval(
            "SELECT accounts_without_resource FROM v_accounts_overview"
        )
    assert my_exhausted is True
    assert int(overview_count) >= 1


@pytest.mark.asyncio
async def test_v_account_error_rate_last_hour(queue_ctx) -> None:
    """Ошибочная попытка попадает в v_account_error_rate_last_hour по аккаунту."""
    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO task_attempts (
                task_id, task_type_id, account_id, attempt_number, status,
                error_code, started_at, finished_at
            ) VALUES ($1, $2, $3, 1, 'error', 'transient_error', now(), now())
            """,
            queue_ctx["task_id"],
            queue_ctx["task_type_id"],
            queue_ctx["account_id"],
        )
        row = await conn.fetchrow(
            "SELECT attempts_last_hour, errors_last_hour, error_rate_percent "
            "FROM v_account_error_rate_last_hour WHERE account_id = $1",
            queue_ctx["account_id"],
        )
    assert row is not None
    assert int(row["attempts_last_hour"]) >= 1
    assert int(row["errors_last_hour"]) >= 1
    assert float(row["error_rate_percent"]) > 0


@pytest.mark.asyncio
async def test_v_task_type_error_rate_last_hour(queue_ctx) -> None:
    """Ошибочная попытка попадает в v_task_type_error_rate_last_hour по типу задачи."""
    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO task_attempts (
                task_id, task_type_id, account_id, attempt_number, status,
                error_code, started_at, finished_at
            ) VALUES ($1, $2, $3, 1, 'error', 'transient_error', now(), now())
            """,
            queue_ctx["task_id"],
            queue_ctx["task_type_id"],
            queue_ctx["account_id"],
        )
        row = await conn.fetchrow(
            "SELECT errors_last_hour, error_rate_percent "
            "FROM v_task_type_error_rate_last_hour WHERE task_type_id = $1",
            queue_ctx["task_type_id"],
        )
    assert row is not None
    assert int(row["errors_last_hour"]) >= 1
    assert float(row["error_rate_percent"]) > 0


@pytest.mark.asyncio
async def test_v_channel_capacity_usage_assigned_channels(queue_ctx) -> None:
    """Назначенный на active-аккаунт канал увеличивает assigned_channels_total."""
    suffix = uuid.uuid4().hex
    external_id = f"{_PREFIX}ch_{suffix}"
    platform_code = f"{_PREFIX}plat_{suffix}"

    async with db.acquire() as conn:
        platform_id = await conn.fetchval(
            "INSERT INTO platforms (code, name) VALUES ($1, $2) RETURNING id",
            platform_code,
            "G7 test platform",
        )
        before = await conn.fetchrow("SELECT * FROM v_channel_capacity_usage")
        await conn.execute(
            """
            INSERT INTO source_channels (
                platform_id, external_channel_id, name,
                assigned_account_id, is_active
            ) VALUES ($1, $2, $3, $4, true)
            """,
            platform_id,
            external_id,
            "G7 test channel",
            queue_ctx["account_id"],
        )
        after = await conn.fetchrow("SELECT * FROM v_channel_capacity_usage")
        await conn.execute(
            "DELETE FROM source_channels WHERE external_channel_id = $1",
            external_id,
        )
        await conn.execute("DELETE FROM platforms WHERE id = $1", platform_id)

    assert int(after["assigned_channels_total"]) == int(before["assigned_channels_total"]) + 1
