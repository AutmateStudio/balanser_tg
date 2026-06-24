"""E3 — integration: retry backoff из task_types → run_after в PG (ТЗ §20, §30.18)."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.errors import RetryableError
from app_balance.queue.mock_adapter import MockTaskAdapter
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.task_attempts import TaskAttemptsRepo
from app_balance.queue.task_queue import TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data
from tests.queue_integration_helpers import (
    AlwaysOkResourceChecker,
    insert_test_account,
    load_in_progress_claimed,
    reclaim_retry_task,
)

_PREFIX = "test_e3_"
_TASK_TYPE = "parser_add_channel"


@pytest.fixture
async def e3_clean(pg_pool):
    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_PREFIX}%",
            session_name_like=f"{_PREFIX}%",
        )

    await _cleanup()
    yield
    await _cleanup()


async def _save_retry_settings() -> dict:
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT retry_delay_seconds, retry_backoff_multiplier, max_retry_delay_seconds
            FROM task_types WHERE code = $1
            """,
            _TASK_TYPE,
        )
    assert row is not None
    return dict(row)


async def _set_retry_settings(
    *,
    retry_delay_seconds: int,
    retry_backoff_multiplier: Decimal,
    max_retry_delay_seconds: int,
) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            """
            UPDATE task_types
            SET retry_delay_seconds = $2,
                retry_backoff_multiplier = $3,
                max_retry_delay_seconds = $4
            WHERE code = $1
            """,
            _TASK_TYPE,
            retry_delay_seconds,
            retry_backoff_multiplier,
            max_retry_delay_seconds,
        )


async def _restore_retry_settings(saved: dict) -> None:
    await _set_retry_settings(
        retry_delay_seconds=int(saved["retry_delay_seconds"]),
        retry_backoff_multiplier=saved["retry_backoff_multiplier"],
        max_retry_delay_seconds=int(saved["max_retry_delay_seconds"]),
    )


async def _insert_in_progress_task(
    *,
    account_id: int,
    max_attempts: int = 5,
    attempt_count: int = 0,
) -> int:
    async with db.acquire() as conn:
        task_type_id = await conn.fetchval(
            "SELECT id FROM task_types WHERE code = $1", _TASK_TYPE
        )
        task_id = await conn.fetchval(
            """
            INSERT INTO task_queue (
                task_type_id, task_type_code, status, priority,
                account_id, payload, dedup_key, max_attempts, attempt_count,
                locked_by, locked_at, locked_until, run_after, started_at
            ) VALUES (
                $1, $2, 'in_progress', 2000000000,
                $3, $4::jsonb, $5, $6, $7,
                $8, now(), now() + interval '1 hour', now(), now()
            )
            RETURNING id
            """,
            task_type_id,
            _TASK_TYPE,
            account_id,
            json.dumps({"ref": "@e3_test"}),
            f"{_PREFIX}{uuid.uuid4().hex}",
            max_attempts,
            attempt_count,
            f"{_PREFIX}lock",
        )
    return int(task_id)


class _RetryableAdapter(MockTaskAdapter):
    async def execute(self, task, *, account) -> None:  # type: ignore[override]
        raise RetryableError("transient_error", "temporary failure")


def _build_dispatcher() -> TaskDispatcher:
    dispatcher = TaskDispatcher(
        queue=TaskQueueRepo(),
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=_RetryableAdapter(),
        resource_check=AlwaysOkResourceChecker(),
        attempts=TaskAttemptsRepo(),
        postpone_delay_seconds=300,
        retry_delay_seconds=60,
    )
    return dispatcher


async def _run_after_remaining_seconds(task_id: int) -> float:
    """Оставшаяся задержка run_after относительно PG now() сразу после dispatch."""
    async with db.acquire() as conn:
        val = await conn.fetchval(
            """
            SELECT EXTRACT(EPOCH FROM (run_after - now()))
            FROM task_queue WHERE id = $1
            """,
            task_id,
        )
    assert val is not None
    return float(val)


def _assert_backoff_remaining(*, remaining: float, expected: int) -> None:
    """finally dispatch на shared PG может «съесть» до ~7s до чтения run_after."""
    assert expected - 8 <= remaining <= expected + 2, (
        f"ожидали ~{expected}s до run_after, получили {remaining}s"
    )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_e3_first_retry_run_after_uses_base_delay(e3_clean) -> None:
    saved = await _save_retry_settings()
    try:
        await _set_retry_settings(
            retry_delay_seconds=10,
            retry_backoff_multiplier=Decimal("2"),
            max_retry_delay_seconds=1800,
        )
        account_id, _ = await insert_test_account(prefix=_PREFIX)
        task_id = await _insert_in_progress_task(account_id=account_id)
        claimed = await load_in_progress_claimed(task_id)
        dispatcher = _build_dispatcher()
        task_type = await TaskTypesRepo().get_by_code(_TASK_TYPE)
        assert task_type is not None

        result = await dispatcher.dispatch(claimed)

        assert result == DispatchResult.RETRIED
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, attempt_count FROM task_queue WHERE id = $1",
                task_id,
            )
        assert row is not None
        assert row["status"] == "retry"
        attempt_count = int(row["attempt_count"])
        assert attempt_count == 1
        expected = dispatcher._calc_retry_delay(
            task_type=task_type,
            attempt_number=attempt_count,
        )
        remaining = await _run_after_remaining_seconds(task_id)
        _assert_backoff_remaining(remaining=remaining, expected=expected)
    finally:
        await _restore_retry_settings(saved)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_e3_second_retry_run_after_uses_backoff(e3_clean) -> None:
    saved = await _save_retry_settings()
    try:
        await _set_retry_settings(
            retry_delay_seconds=10,
            retry_backoff_multiplier=Decimal("2"),
            max_retry_delay_seconds=1800,
        )
        account_id, _ = await insert_test_account(prefix=_PREFIX)
        task_id = await _insert_in_progress_task(
            account_id=account_id,
            attempt_count=1,
        )
        claimed = await load_in_progress_claimed(task_id)
        dispatcher = _build_dispatcher()
        task_type = await TaskTypesRepo().get_by_code(_TASK_TYPE)
        assert task_type is not None
        expected = dispatcher._calc_retry_delay(
            task_type=task_type,
            attempt_number=2,
        )

        result = await dispatcher.dispatch(claimed)

        assert result == DispatchResult.RETRIED
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, attempt_count FROM task_queue WHERE id = $1",
                task_id,
            )
        assert row is not None
        assert int(row["attempt_count"]) == 2
        remaining = await _run_after_remaining_seconds(task_id)
        _assert_backoff_remaining(remaining=remaining, expected=expected)
    finally:
        await _restore_retry_settings(saved)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_e3_retry_backoff_respects_max_delay(e3_clean) -> None:
    saved = await _save_retry_settings()
    try:
        await _set_retry_settings(
            retry_delay_seconds=10,
            retry_backoff_multiplier=Decimal("3"),
            max_retry_delay_seconds=15,
        )
        account_id, _ = await insert_test_account(prefix=_PREFIX)
        task_id = await _insert_in_progress_task(
            account_id=account_id,
            attempt_count=2,
        )
        claimed = await load_in_progress_claimed(task_id)
        dispatcher = _build_dispatcher()
        task_type = await TaskTypesRepo().get_by_code(_TASK_TYPE)
        assert task_type is not None
        expected = dispatcher._calc_retry_delay(
            task_type=task_type,
            attempt_number=3,
        )
        assert expected == 15

        await dispatcher.dispatch(claimed)

        remaining = await _run_after_remaining_seconds(task_id)
        _assert_backoff_remaining(remaining=remaining, expected=expected)
        assert remaining < 25, "без cap delay был бы ~40s"
    finally:
        await _restore_retry_settings(saved)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_e3_exhausted_max_attempts_becomes_failed(e3_clean) -> None:
    account_id, _ = await insert_test_account(prefix=_PREFIX)
    task_id = await _insert_in_progress_task(
        account_id=account_id,
        max_attempts=1,
    )
    claimed = await load_in_progress_claimed(task_id)

    async with db.acquire() as conn:
        old_run_after = await conn.fetchval(
            "SELECT run_after FROM task_queue WHERE id = $1", task_id
        )

    result = await _build_dispatcher().dispatch(claimed)
    assert result == DispatchResult.FAILED

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, run_after, attempt_count, finished_at FROM task_queue WHERE id = $1",
            task_id,
        )
    assert row is not None
    assert row["status"] == "failed"
    assert int(row["attempt_count"]) == 1
    assert row["finished_at"] is not None
    assert row["run_after"] == old_run_after
