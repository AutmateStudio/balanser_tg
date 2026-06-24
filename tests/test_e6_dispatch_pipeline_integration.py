"""E6 — integration: идемпотентный multi-op пайплайн через TaskDispatcher (E8)."""
from __future__ import annotations

import json
import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.adapter import execute_multi_op_pipeline
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.error_codes import ErrorCode
from app_balance.queue.errors import RetryableError
from app_balance.queue.ops_catalog import COLLECT_EXTRA_DATA, TASK_TYPE_OPS
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.resource_usage import ResourceUsageRepo
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

_PREFIX = "test_e6_dispatch_"
_FAIL_AT_INDEX = 2


@pytest.fixture
async def e6_clean(pg_pool):
    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_PREFIX}%",
            session_name_like=f"{_PREFIX}%",
        )

    await _cleanup()
    yield
    await _cleanup()


async def _enable_collect_task_type() -> bool:
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_enabled FROM task_types WHERE code = $1",
            COLLECT_EXTRA_DATA,
        )
        if row is None:
            pytest.skip("collect_extra_data отсутствует в seed")
        was_enabled = bool(row["is_enabled"])
        if not was_enabled:
            await conn.execute(
                "UPDATE task_types SET is_enabled = true WHERE code = $1",
                COLLECT_EXTRA_DATA,
            )
        return was_enabled


async def _restore_collect_enabled(was_enabled: bool) -> None:
    if was_enabled:
        return
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_types SET is_enabled = false WHERE code = $1",
            COLLECT_EXTRA_DATA,
        )


async def _insert_in_progress_collect_task(*, account_id: int) -> int:
    task_type = await TaskTypesRepo().get_by_code(COLLECT_EXTRA_DATA)
    assert task_type is not None
    async with db.acquire() as conn:
        task_id = await conn.fetchval(
            """
            INSERT INTO task_queue (
                task_type_id, task_type_code, status, priority,
                account_id, payload, dedup_key, max_attempts,
                locked_by, locked_at, locked_until, run_after, started_at
            ) VALUES (
                $1, $2, 'in_progress', 2000000000,
                $3, $4::jsonb, $5, 5,
                $6, now(), now() + interval '1 hour', now(), now()
            )
            RETURNING id
            """,
            task_type.id,
            COLLECT_EXTRA_DATA,
            account_id,
            json.dumps({"ref": "@e6_collect"}),
            f"{_PREFIX}{uuid.uuid4().hex}",
            f"{_PREFIX}lock",
        )
    return int(task_id)


class _CollectPipelineAdapter:
    def __init__(self, *, fail_at_index: int | None = None) -> None:
        self.fail_at_index = fail_at_index
        self.executed_ops: list[str] = []

    async def execute(self, task, *, account) -> None:
        task_type = await TaskTypesRepo().get_by_code(COLLECT_EXTRA_DATA)
        assert task_type is not None
        op_index = 0

        async def execute_op(step) -> None:
            nonlocal op_index
            if self.fail_at_index is not None and op_index == self.fail_at_index:
                raise RetryableError(
                    ErrorCode.TRANSIENT_ERROR,
                    f"fail at {step.op_code}",
                )
            self.executed_ops.append(step.op_code)
            op_index += 1

        await execute_multi_op_pipeline(
            task,
            task_type=task_type,
            account=account,
            execute_op=execute_op,
            queue=TaskQueueRepo(),
            usage=ResourceUsageRepo(),
        )


def _build_dispatcher(adapter: _CollectPipelineAdapter) -> TaskDispatcher:
    return TaskDispatcher(
        queue=TaskQueueRepo(),
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=adapter,
        resource_check=AlwaysOkResourceChecker(),
        attempts=TaskAttemptsRepo(),
        postpone_delay_seconds=300,
        retry_delay_seconds=0,
    )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_e6_dispatch_pipeline_retries_from_failed_op_without_duplicates(
    e6_clean,
) -> None:
    """E8 через TaskDispatcher: retry продолжает с упавшего op."""
    was_enabled = await _enable_collect_task_type()
    pipeline_codes = [op.op_code for op in TASK_TYPE_OPS[COLLECT_EXTRA_DATA]]
    try:
        account_id, _ = await insert_test_account(prefix=_PREFIX)
        task_id = await _insert_in_progress_collect_task(account_id=account_id)

        adapter = _CollectPipelineAdapter(fail_at_index=_FAIL_AT_INDEX)
        result1 = await _build_dispatcher(adapter).dispatch(
            await load_in_progress_claimed(task_id)
        )
        assert result1 == DispatchResult.RETRIED
        assert adapter.executed_ops == pipeline_codes[:_FAIL_AT_INDEX]

        async with db.acquire() as conn:
            payload = await conn.fetchval(
                "SELECT payload FROM task_queue WHERE id = $1", task_id
            )
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload.get("last_completed_step") == pipeline_codes[_FAIL_AT_INDEX - 1]

        adapter2 = _CollectPipelineAdapter()
        claimed2 = await reclaim_retry_task(task_id, locked_by=f"{_PREFIX}retry")
        result2 = await _build_dispatcher(adapter2).dispatch(claimed2)
        assert result2 == DispatchResult.COMPLETED
        assert adapter2.executed_ops == pipeline_codes[_FAIL_AT_INDEX:]
    finally:
        await _restore_collect_enabled(was_enabled)
