"""G6 — unit/integration тесты детектора повторяющихся ошибок per-op (§30.20)."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app_balance.queue import db
from app_balance.queue.error_codes import ErrorCode
from app_balance.queue.monitoring.config import AlertConfig, ErrorDetectorConfig
from app_balance.queue.monitoring.error_detector import (
    calc_reduced_rph,
    evaluate_adjustments,
    run_detector_tick,
)
from app_balance.queue.monitoring.error_detector_repo import (
    ErrorDetectorRepo,
    RecurringErrorRow,
)
from app_balance.queue.monitoring.notify import AlertNotifier
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_g6_"
_OP_GET_ENTITY = "get_entity"


def _detector_config(**overrides) -> ErrorDetectorConfig:
    base = ErrorDetectorConfig(
        enabled=True,
        window_seconds=3600,
        min_count=5,
        rph_factor=0.7,
        min_rph=2,
        repeat_window_seconds=86400,
        cooldown_seconds=3600,
        telegram_chat_id="-100123",
        bot_token="test-bot",
    )
    return replace(base, **overrides)


def _alert_config() -> AlertConfig:
    return AlertConfig(enabled=False, webhook_url="")


def _row(
    *,
    error_code: str = ErrorCode.FLOOD_WAIT,
    op_code: str = _OP_GET_ENTITY,
    op_type_id: int = 1,
    current_rph_limit: int = 7,
    error_count: int = 5,
    last_account_id: int | None = 42,
) -> RecurringErrorRow:
    return RecurringErrorRow(
        error_code=error_code,
        op_code=op_code,
        op_type_id=op_type_id,
        current_rph_limit=current_rph_limit,
        error_count=error_count,
        last_error_at=datetime.now(timezone.utc),
        last_account_id=last_account_id,
    )


def test_calc_reduced_rph_default_formula() -> None:
    assert calc_reduced_rph(7, factor=0.7, min_rph=2) == 4
    assert calc_reduced_rph(2, factor=0.7, min_rph=2) == 2


def test_evaluate_flood_wait_reduce_rph() -> None:
    config = _detector_config()
    plans = evaluate_adjustments(
        [_row(current_rph_limit=7, error_count=5)],
        config=config,
        adjustment_counts_24h={},
        debounced_pairs=frozenset(),
    )
    assert len(plans) == 1
    plan = plans[0]
    assert plan.action == "reduce_rph"
    assert plan.new_rph_limit == 4
    assert plan.severity == "WARNING"
    assert plan.apply_cooldown is False


def test_evaluate_peer_flood_reduce_and_cooldown() -> None:
    config = _detector_config()
    plans = evaluate_adjustments(
        [_row(error_code=ErrorCode.PEER_FLOOD, current_rph_limit=10)],
        config=config,
        adjustment_counts_24h={},
        debounced_pairs=frozenset(),
    )
    assert len(plans) == 1
    plan = plans[0]
    assert plan.action == "reduce_rph"
    assert plan.new_rph_limit == 7
    assert plan.apply_cooldown is True


def test_evaluate_second_adjustment_within_24h_disables_op() -> None:
    config = _detector_config()
    key = (ErrorCode.FLOOD_WAIT, _OP_GET_ENTITY)
    plans = evaluate_adjustments(
        [_row()],
        config=config,
        adjustment_counts_24h={key: 1},
        debounced_pairs=frozenset(),
    )
    assert len(plans) == 1
    plan = plans[0]
    assert plan.action == "disable_op"
    assert plan.severity == "CRITICAL"
    assert plan.new_rph_limit is None


def test_evaluate_debounce_skips_pair_in_window() -> None:
    config = _detector_config()
    key = (ErrorCode.FLOOD_WAIT, _OP_GET_ENTITY)
    plans = evaluate_adjustments(
        [_row()],
        config=config,
        adjustment_counts_24h={},
        debounced_pairs=frozenset({key}),
    )
    assert plans == []


def test_evaluate_ignores_untracked_error_codes() -> None:
    config = _detector_config()
    plans = evaluate_adjustments(
        [_row(error_code="transient_error")],
        config=config,
        adjustment_counts_24h={},
        debounced_pairs=frozenset(),
    )
    assert plans == []


async def _cleanup() -> None:
    async with db.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM resource_limit_adjustments
            WHERE op_code = $1
               OR account_id IN (
                    SELECT id FROM accounts WHERE session_name LIKE $2
               )
            """,
            _OP_GET_ENTITY,
            f"{_PREFIX}%",
        )
        await conn.execute(
            """
            UPDATE resource_op_types
            SET rph_limit = 7, is_enabled = true, updated_at = now()
            WHERE code = $1
            """,
            _OP_GET_ENTITY,
        )
    await cleanup_queue_test_data(
        dedup_key_like=f"{_PREFIX}%",
        session_name_like=f"{_PREFIX}%",
    )


@pytest.fixture
async def g6_ctx(pg_pool):
    await _cleanup()
    session_name = f"{_PREFIX}{uuid.uuid4().hex}"
    dedup_key = f"{_PREFIX}{uuid.uuid4().hex}"

    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )
        task_type_id = await conn.fetchval(
            "SELECT id FROM task_types WHERE code = 'parser_add_channel'"
        )
        op_row = await conn.fetchrow(
            "SELECT id, rph_limit FROM resource_op_types WHERE code = $1",
            _OP_GET_ENTITY,
        )

    enqueue = await TaskQueueRepo().enqueue(
        EnqueueInput(task_type_code="parser_add_channel", dedup_key=dedup_key)
    )

    ctx = {
        "account_id": account_id,
        "task_id": enqueue.task_id,
        "task_type_id": task_type_id,
        "op_type_id": int(op_row["id"]),
    }
    yield ctx
    await _cleanup()


async def _seed_failed_attempts(
    *,
    account_id: int,
    task_id: int,
    task_type_id: int,
    op_type_id: int,
    error_code: str,
    count: int,
) -> None:
    async with db.acquire() as conn:
        for attempt_number in range(1, count + 1):
            attempt_id = await conn.fetchval(
                """
                INSERT INTO task_attempts (
                    task_id, task_type_id, account_id, attempt_number, status,
                    error_code, started_at, finished_at
                ) VALUES ($1, $2, $3, $4, 'error', $5, now(), now())
                RETURNING id
                """,
                task_id,
                task_type_id,
                account_id,
                attempt_number,
                error_code,
            )
            await conn.execute(
                """
                INSERT INTO account_resource_usage (
                    account_id, op_type_id, task_id, task_attempt_id,
                    task_type_id, units
                ) VALUES ($1, $2, $3, $4, $5, 1)
                """,
                account_id,
                op_type_id,
                task_id,
                attempt_id,
                task_type_id,
            )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_v_recurring_errors_window_counts_pattern(g6_ctx) -> None:
    await _seed_failed_attempts(
        account_id=g6_ctx["account_id"],
        task_id=g6_ctx["task_id"],
        task_type_id=g6_ctx["task_type_id"],
        op_type_id=g6_ctx["op_type_id"],
        error_code=ErrorCode.FLOOD_WAIT,
        count=5,
    )
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT error_count, op_code, error_code
            FROM v_recurring_errors_window
            WHERE error_code = $1 AND op_code = $2
            """,
            ErrorCode.FLOOD_WAIT,
            _OP_GET_ENTITY,
        )
    assert row is not None
    assert int(row["error_count"]) >= 5


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_flood_wait_reduces_rph_and_writes_audit(g6_ctx) -> None:
    """§30.20: 5× flood_wait на op → RPH↓, audit, dev notify."""
    config = _detector_config(min_count=5)
    repo = ErrorDetectorRepo()

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE resource_op_types SET rph_limit = 7 WHERE id = $1",
            g6_ctx["op_type_id"],
        )

    await _seed_failed_attempts(
        account_id=g6_ctx["account_id"],
        task_id=g6_ctx["task_id"],
        task_type_id=g6_ctx["task_type_id"],
        op_type_id=g6_ctx["op_type_id"],
        error_code=ErrorCode.FLOOD_WAIT,
        count=5,
    )

    notifier = AlertNotifier(_alert_config())
    with patch(
        "app_balance.queue.monitoring.error_detector.send_telegram_dev",
        new_callable=AsyncMock,
    ) as tg_mock:
        applied = await run_detector_tick(
            repo,
            config,
            notifier=notifier,
            alert_config=_alert_config(),
        )

    assert applied == 1
    tg_mock.assert_awaited()

    async with db.acquire() as conn:
        new_rph = await conn.fetchval(
            "SELECT rph_limit FROM resource_op_types WHERE id = $1",
            g6_ctx["op_type_id"],
        )
        audit = await conn.fetchrow(
            """
            SELECT action, old_rph_limit, new_rph_limit, error_count
            FROM resource_limit_adjustments
            WHERE error_code = $1 AND op_code = $2
            ORDER BY id DESC LIMIT 1
            """,
            ErrorCode.FLOOD_WAIT,
            _OP_GET_ENTITY,
        )

    assert int(new_rph) == 4
    assert audit is not None
    assert audit["action"] == "reduce_rph"
    assert int(audit["old_rph_limit"]) == 7
    assert int(audit["new_rph_limit"]) == 4
    assert int(audit["error_count"]) >= 5


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_repeat_adjustment_disables_op(g6_ctx) -> None:
    config = _detector_config(min_count=5)
    repo = ErrorDetectorRepo()

    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO resource_limit_adjustments (
                error_code, op_code, op_type_id, action,
                old_rph_limit, new_rph_limit, account_id,
                error_count, window_seconds, created_at
            ) VALUES (
                $1, $2, $3, 'reduce_rph', 7, 4, $4, 5, 3600,
                now() - interval '2 hours'
            )
            """,
            ErrorCode.FLOOD_WAIT,
            _OP_GET_ENTITY,
            g6_ctx["op_type_id"],
            g6_ctx["account_id"],
        )
        await conn.execute(
            "UPDATE resource_op_types SET rph_limit = 4, is_enabled = true WHERE id = $1",
            g6_ctx["op_type_id"],
        )

    await _seed_failed_attempts(
        account_id=g6_ctx["account_id"],
        task_id=g6_ctx["task_id"],
        task_type_id=g6_ctx["task_type_id"],
        op_type_id=g6_ctx["op_type_id"],
        error_code=ErrorCode.FLOOD_WAIT,
        count=5,
    )

    with patch(
        "app_balance.queue.monitoring.error_detector.send_telegram_dev",
        new_callable=AsyncMock,
    ):
        applied = await run_detector_tick(
            repo,
            config,
            notifier=AlertNotifier(_alert_config()),
            alert_config=_alert_config(),
        )

    assert applied == 1

    async with db.acquire() as conn:
        enabled = await conn.fetchval(
            "SELECT is_enabled FROM resource_op_types WHERE id = $1",
            g6_ctx["op_type_id"],
        )
        audit = await conn.fetchval(
            """
            SELECT action FROM resource_limit_adjustments
            WHERE error_code = $1 AND op_code = $2
            ORDER BY id DESC LIMIT 1
            """,
            ErrorCode.FLOOD_WAIT,
            _OP_GET_ENTITY,
        )

    assert enabled is False
    assert audit == "disable_op"


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_debounce_second_tick_no_op(g6_ctx) -> None:
    config = _detector_config(min_count=5)
    repo = ErrorDetectorRepo()

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE resource_op_types SET rph_limit = 7 WHERE id = $1",
            g6_ctx["op_type_id"],
        )

    await _seed_failed_attempts(
        account_id=g6_ctx["account_id"],
        task_id=g6_ctx["task_id"],
        task_type_id=g6_ctx["task_type_id"],
        op_type_id=g6_ctx["op_type_id"],
        error_code=ErrorCode.FLOOD_WAIT,
        count=5,
    )

    with patch(
        "app_balance.queue.monitoring.error_detector.send_telegram_dev",
        new_callable=AsyncMock,
    ):
        first = await run_detector_tick(repo, config)
        second = await run_detector_tick(repo, config)

    assert first == 1
    assert second == 0


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_peer_flood_applies_cooldown(g6_ctx) -> None:
    config = _detector_config(min_count=5)
    accounts = MagicMock()
    accounts.set_cooldown = AsyncMock()
    repo = ErrorDetectorRepo(accounts=accounts)

    async with db.acquire() as conn:
        session_name = await conn.fetchval(
            "SELECT session_name FROM accounts WHERE id = $1",
            g6_ctx["account_id"],
        )
        await conn.execute(
            "UPDATE resource_op_types SET rph_limit = 10 WHERE id = $1",
            g6_ctx["op_type_id"],
        )

    await _seed_failed_attempts(
        account_id=g6_ctx["account_id"],
        task_id=g6_ctx["task_id"],
        task_type_id=g6_ctx["task_type_id"],
        op_type_id=g6_ctx["op_type_id"],
        error_code=ErrorCode.PEER_FLOOD,
        count=5,
    )

    with patch(
        "app_balance.queue.monitoring.error_detector.send_telegram_dev",
        new_callable=AsyncMock,
    ):
        applied = await run_detector_tick(repo, config)

    assert applied == 1
    accounts.set_cooldown.assert_awaited_once()
    call_args = accounts.set_cooldown.await_args
    assert call_args.args[0] == session_name


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_detector_tick_emits_critical_alert_on_disable(g6_ctx) -> None:
    config = _detector_config(min_count=5)
    repo = ErrorDetectorRepo()

    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO resource_limit_adjustments (
                error_code, op_code, op_type_id, action,
                old_rph_limit, new_rph_limit, account_id,
                error_count, window_seconds, created_at
            ) VALUES (
                $1, $2, $3, 'reduce_rph', 7, 4, $4, 5, 3600,
                now() - interval '2 hours'
            )
            """,
            ErrorCode.FLOOD_WAIT,
            _OP_GET_ENTITY,
            g6_ctx["op_type_id"],
            g6_ctx["account_id"],
        )

    await _seed_failed_attempts(
        account_id=g6_ctx["account_id"],
        task_id=g6_ctx["task_id"],
        task_type_id=g6_ctx["task_type_id"],
        op_type_id=g6_ctx["op_type_id"],
        error_code=ErrorCode.FLOOD_WAIT,
        count=5,
    )

    notifier = AlertNotifier(_alert_config())
    notifier.emit = AsyncMock(return_value=True)

    with patch(
        "app_balance.queue.monitoring.error_detector.send_telegram_dev",
        new_callable=AsyncMock,
    ):
        await run_detector_tick(
            repo,
            config,
            notifier=notifier,
            alert_config=_alert_config(),
        )

    assert notifier.emit.await_count == 1
    alert = notifier.emit.await_args.args[0]
    assert alert.code == "error_detector_disable_op"
    assert alert.severity == "CRITICAL"
