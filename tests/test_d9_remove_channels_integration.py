"""D9 — integration: enqueue parser_remove_channel → dispatch → done/retry (PG)."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.error_codes import ErrorCode
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
)
from tests.tz30.conftest import task_attempts_for_task

_PREFIX = "test_d9_"


async def _require_remove_task_type() -> None:
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_enabled FROM task_types WHERE code = 'parser_remove_channel'"
        )
    if row is None:
        pytest.skip("parser_remove_channel отсутствует в seed (D9)")
    if not row["is_enabled"]:
        pytest.skip("parser_remove_channel выключен в seed")


@pytest.fixture
async def d9_clean(pg_pool):
    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_PREFIX}%",
            session_name_like=f"{_PREFIX}%",
        )

    await _cleanup()
    yield
    await _cleanup()


def _build_dispatcher(adapter) -> TaskDispatcher:
    return TaskDispatcher(
        queue=TaskQueueRepo(),
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=adapter,
        resource_check=AlwaysOkResourceChecker(),
        attempts=TaskAttemptsRepo(),
        postpone_delay_seconds=300,
        retry_delay_seconds=60,
    )


async def _insert_in_progress_remove_task(*, account_id: int, payload: dict) -> int:
    async with db.acquire() as conn:
        task_type_id = await conn.fetchval(
            "SELECT id FROM task_types WHERE code = 'parser_remove_channel'"
        )
        task_id = await conn.fetchval(
            """
            INSERT INTO task_queue (
                task_type_id, task_type_code, status, priority,
                account_id, payload, dedup_key, max_attempts,
                locked_by, locked_at, locked_until, run_after, started_at
            ) VALUES (
                $1, 'parser_remove_channel', 'in_progress', 2000000000,
                $2, $3::jsonb, $4, 5,
                $5, now(), now() + interval '1 hour', now(), now()
            )
            RETURNING id
            """,
            task_type_id,
            account_id,
            json.dumps(payload),
            f"{_PREFIX}{uuid.uuid4().hex}",
            f"{_PREFIX}lock",
        )
    return int(task_id)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_d9_enqueue_parser_remove_creates_tasks_in_pg(d9_clean) -> None:
    await _require_remove_task_type()
    from discovery_api.queue.producer import enqueue_parser_remove_channels

    account_id, session_name = await insert_test_account(prefix=f"{_PREFIX}enq_")
    clump = MagicMock()
    clump.assignments = {"@ch_a": session_name, "@ch_b": session_name}

    with (
        patch("discovery_api.session_registry.get_clump", return_value=clump),
        patch(
            "app_balance.queue.source_channels.SourceChannelsRepo.find_id_by_ref",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await enqueue_parser_remove_channels(
            parser_id="pid_d9",
            channel_list=["@ch_a", "@ch_b"],
            action_id=f"{_PREFIX}action",
        )

    assert len(result.task_ids) == 2
    dedup_keys: set[str] = set()
    async with db.acquire() as conn:
        for task_id in result.task_ids:
            row = await conn.fetchrow(
                """
                SELECT task_type_code, account_id, dedup_key, payload
                FROM task_queue WHERE id = $1
                """,
                task_id,
            )
            assert row is not None
            assert row["task_type_code"] == "parser_remove_channel"
            assert int(row["account_id"]) == account_id
            dedup_keys.add(row["dedup_key"])
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            assert payload["parser_id"] == "pid_d9"
    assert len(dedup_keys) == 2


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_d9_dispatch_parser_remove_completes_task(d9_clean) -> None:
    await _require_remove_task_type()
    from app_balance.queue.adapter import ClumpTaskAdapter

    account_id, session_name = await insert_test_account(prefix=f"{_PREFIX}rm_")
    task_id = await _insert_in_progress_remove_task(
        account_id=account_id,
        payload={
            "parser_id": "pid_d9",
            "channel_ref": "@remove_me",
            "action_id": f"{_PREFIX}act",
        },
    )

    clump = MagicMock()
    clump.remove_channel = AsyncMock(return_value=True)
    clump.start = AsyncMock()
    clump.session_name_list = [session_name]
    clump.has_session = MagicMock(return_value=True)

    adapter = ClumpTaskAdapter(clump_getter=lambda _pid: clump)
    with patch(
        "app_balance.queue.channel_assignment_sync.sync_after_parser_remove_channel",
        new_callable=AsyncMock,
    ):
        result = await _build_dispatcher(adapter).dispatch(
            await load_in_progress_claimed(task_id)
        )

    assert result == DispatchResult.COMPLETED
    async with db.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", task_id
        )
    assert status == "done"
    attempts = await task_attempts_for_task(task_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "success"


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_d9_remove_retry_on_clump_not_loaded(d9_clean) -> None:
    await _require_remove_task_type()
    account_id, _ = await insert_test_account(prefix=f"{_PREFIX}retry_")
    task_id = await _insert_in_progress_remove_task(
        account_id=account_id,
        payload={"parser_id": "missing", "channel_ref": "@x"},
    )

    class ClumpMissingAdapter(MockTaskAdapter):
        async def execute(self, task, *, account) -> None:  # type: ignore[override]
            raise RetryableError(
                ErrorCode.CLUMP_NOT_LOADED,
                f"{ErrorCode.CLUMP_NOT_LOADED}:missing",
            )

    result = await _build_dispatcher(ClumpMissingAdapter()).dispatch(
        await load_in_progress_claimed(task_id)
    )
    assert result == DispatchResult.RETRIED

    async with db.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1", task_id
        )
    assert status == "retry"
    attempts = await task_attempts_for_task(task_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "error"
    assert attempts[0]["error_code"] == ErrorCode.CLUMP_NOT_LOADED
