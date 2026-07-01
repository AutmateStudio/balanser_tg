"""D7 — integration-тесты dual-write assigned_account_id через adapter/dispatch."""
from __future__ import annotations

import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.adapter import ClumpTaskAdapter, execute_task
from app_balance.queue.channel_assignment_sync import sync_after_parser_add_channel
from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.resource_check import ResourceChecker
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.source_channels import SourceChannelsRepo
from app_balance.queue.task_queue import ClaimedTask, EnqueueInput, TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data
from tests.test_adapter import FakeClump, _account

_PREFIX = "test_d7_"
_TEST_PRIO = 2_000_000_000


async def _cleanup(prefix: str = _PREFIX) -> None:
    await cleanup_queue_test_data(
        dedup_key_like=f"{prefix}%",
        session_name_like=f"{prefix}%",
    )
    async with db.acquire() as conn:
        await conn.execute(
            """
            UPDATE source_channels
            SET assigned_account_id = NULL
            WHERE external_channel_id LIKE $1
            """,
            f"{prefix}%",
        )
        await conn.execute(
            "DELETE FROM source_channels WHERE external_channel_id LIKE $1",
            f"{prefix}%",
        )
        await conn.execute(
            "DELETE FROM platforms WHERE code LIKE $1",
            f"{prefix}%",
        )


@pytest.fixture
async def d7_add_ctx(pg_pool):
    await _cleanup()
    suffix = uuid.uuid4().hex
    platform_code = f"{_PREFIX}plat_{suffix}"
    external_id = f"{_PREFIX}ch_{suffix}"
    session_name = f"{_PREFIX}acc_{suffix}"

    async with db.acquire() as conn:
        platform_id = await conn.fetchval(
            "INSERT INTO platforms (code, name) VALUES ($1, $2) RETURNING id",
            platform_code,
            "D7 add platform",
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
            "D7 channel",
        )

    channel_ref = "@d7_add"
    dedup_key = f"{_PREFIX}add_{suffix}"
    enqueue = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="parser_add_channel",
            dedup_key=dedup_key,
            priority=_TEST_PRIO,
            account_id=account_id,
            channel_id=channel_id,
            payload={
                "parser_id": "p1",
                "channel_ref": channel_ref,
            },
        )
    )
    assert enqueue.created and enqueue.task_id is not None

    yield {
        "account_id": account_id,
        "channel_id": channel_id,
        "task_id": enqueue.task_id,
        "session_name": session_name,
        "channel_ref": channel_ref,
    }
    await _cleanup()


@pytest.fixture
async def d7_move_ctx(pg_pool):
    await _cleanup(f"{_PREFIX}move_")
    suffix = uuid.uuid4().hex
    platform_code = f"{_PREFIX}move_plat_{suffix}"
    external_id = f"{_PREFIX}move_ch_{suffix}"
    source_name = f"{_PREFIX}move_src_{suffix}"
    target_name = f"{_PREFIX}move_tgt_{suffix}"

    async with db.acquire() as conn:
        platform_id = await conn.fetchval(
            "INSERT INTO platforms (code, name) VALUES ($1, $2) RETURNING id",
            platform_code,
            "D7 move platform",
        )
        source_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            source_name,
        )
        target_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            target_name,
        )
        channel_id = await conn.fetchval(
            "INSERT INTO source_channels (platform_id, external_channel_id, name) "
            "VALUES ($1, $2, $3) RETURNING id",
            platform_id,
            external_id,
            "D7 move channel",
        )

    dedup_key = f"{_PREFIX}move_{suffix}"
    enqueue = await TaskQueueRepo().enqueue(
        EnqueueInput(
            task_type_code="move_channel",
            dedup_key=dedup_key,
            priority=_TEST_PRIO,
            source_account_id=source_id,
            target_account_id=target_id,
            channel_id=channel_id,
            payload={
                "parser_id": "p1",
                "channel_ref": "@d7_move",
                "webhook_url": "https://hook.example/d7",
            },
        )
    )
    assert enqueue.created and enqueue.task_id is not None

    yield {
        "source_id": source_id,
        "target_id": target_id,
        "source_name": source_name,
        "target_name": target_name,
        "channel_id": channel_id,
        "task_id": enqueue.task_id,
    }
    await _cleanup(f"{_PREFIX}move_")


def _dispatcher_with_clump(clump: FakeClump) -> TaskDispatcher:
    accounts_repo = AccountsRepo()
    usage = ResourceUsageRepo()
    return TaskDispatcher(
        queue=TaskQueueRepo(),
        accounts=accounts_repo,
        task_types=TaskTypesRepo(),
        adapter=ClumpTaskAdapter(
            clump_getter=lambda _pid: clump,
            account_getter=accounts_repo.get_by_id,
        ),
        usage=usage,
        resource_check=ResourceChecker(usage),
        postpone_delay_seconds=300,
    )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_add_dual_writes_assigned_account(d7_add_ctx) -> None:
    ctx = d7_add_ctx
    clump = FakeClump()
    repo = TaskQueueRepo()
    claimed = await repo.claim_by_id(ctx["task_id"], locked_by="d7-it")
    assert claimed is not None
    assert claimed.id == ctx["task_id"]
    assert claimed.channel_id == ctx["channel_id"]

    result = await _dispatcher_with_clump(clump).dispatch(claimed)

    assert result == DispatchResult.COMPLETED
    assigned = await SourceChannelsRepo().get_assigned_account(ctx["channel_id"])
    assert assigned == ctx["account_id"]


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_execute_add_dual_write_direct(d7_add_ctx) -> None:
    ctx = d7_add_ctx
    clump = FakeClump()
    repo = TaskQueueRepo()
    claimed = await repo.claim_by_id(ctx["task_id"], locked_by="d7-direct")
    assert claimed is not None

    account = await AccountsRepo().get_by_id(ctx["account_id"])
    assert account is not None

    await execute_task(
        claimed,
        account=account,
        clump_getter=lambda _pid: clump,
    )

    assigned = await SourceChannelsRepo().get_assigned_account(ctx["channel_id"])
    assert assigned == ctx["account_id"]


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_move_dual_writes_target_account(d7_move_ctx) -> None:
    ctx = d7_move_ctx
    clump = FakeClump()
    repo = TaskQueueRepo()
    claimed = await repo.claim_by_id(ctx["task_id"], locked_by="d7-move-it")
    assert claimed is not None
    assert claimed.id == ctx["task_id"]

    result = await _dispatcher_with_clump(clump).dispatch(claimed)

    assert result == DispatchResult.COMPLETED
    assigned = await SourceChannelsRepo().get_assigned_account(ctx["channel_id"])
    assert assigned == ctx["target_id"]


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_execute_move_dual_write_direct(d7_move_ctx) -> None:
    ctx = d7_move_ctx
    clump = FakeClump()
    repo = TaskQueueRepo()
    claimed = await repo.claim_by_id(ctx["task_id"], locked_by="d7-move-direct")
    assert claimed is not None

    accounts_repo = AccountsRepo()
    target = await accounts_repo.get_by_id(ctx["target_id"])
    assert target is not None

    await execute_task(
        claimed,
        account=target,
        clump_getter=lambda _pid: clump,
        account_getter=accounts_repo.get_by_id,
    )

    assigned = await SourceChannelsRepo().get_assigned_account(ctx["channel_id"])
    assert assigned == ctx["target_id"]


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_execute_add_missing_channel_row_raises(pg_pool) -> None:
    clump = FakeClump()
    task = ClaimedTask(
        id=1,
        task_type_id=1,
        task_type_code="parser_add_channel",
        priority=500,
        payload={"parser_id": "p1", "channel_ref": "@missing"},
        channel_id=9_999_999_999,
        account_id=1,
        source_account_id=None,
        target_account_id=None,
        attempt_count=1,
        max_attempts=3,
        dedup_key=None,
        locked_by="test",
        locked_until=None,
    )

    with pytest.raises(RuntimeError, match="source_channel_not_found"):
        await execute_task(
            task,
            account=_account(),
            clump_getter=lambda _pid: clump,
            sync_after_add=sync_after_parser_add_channel,
        )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_add_clump_error_no_pg_write(d7_add_ctx) -> None:
    ctx = d7_add_ctx
    clump = FakeClump()
    clump.add_channel_on_session.return_value = {
        "channel": ctx["channel_ref"],
        "session_name": ctx["session_name"],
        "chat_id": None,
        "error": "join_failed",
    }
    repo = TaskQueueRepo()
    claimed = await repo.claim_by_id(ctx["task_id"], locked_by="d7-err")
    assert claimed is not None

    result = await _dispatcher_with_clump(clump).dispatch(claimed)

    assert result == DispatchResult.RETRIED
    assigned = await SourceChannelsRepo().get_assigned_account(ctx["channel_id"])
    assert assigned is None


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_add_missing_channel_row_retries(d7_add_ctx) -> None:
    ctx = d7_add_ctx
    clump = FakeClump()
    repo = TaskQueueRepo()
    claimed = await repo.claim_by_id(ctx["task_id"], locked_by="d7-retry")
    assert claimed is not None

    broken_task = ClaimedTask(
        id=claimed.id,
        task_type_id=claimed.task_type_id,
        task_type_code=claimed.task_type_code,
        priority=claimed.priority,
        payload=claimed.payload,
        channel_id=9_999_999_999,
        account_id=claimed.account_id,
        source_account_id=claimed.source_account_id,
        target_account_id=claimed.target_account_id,
        attempt_count=claimed.attempt_count,
        max_attempts=claimed.max_attempts,
        dedup_key=claimed.dedup_key,
        locked_by=claimed.locked_by,
        locked_until=claimed.locked_until,
    )

    result = await _dispatcher_with_clump(clump).dispatch(broken_task)

    assert result == DispatchResult.RETRIED
    assigned = await SourceChannelsRepo().get_assigned_account(ctx["channel_id"])
    assert assigned is None

    async with db.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM task_queue WHERE id = $1",
            ctx["task_id"],
        )
    assert status == "retry"


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_add_without_task_channel_id_resolves_from_ref(pg_pool) -> None:
    """Prod-сценарий: задача без channel_id, dual-write по payload.channel_ref."""
    import uuid

    suffix = uuid.uuid4().hex
    platform_code = f"{_PREFIX}ref_plat_{suffix}"
    handle = f"d7refonly_{suffix}"
    channel_ref = f"@{handle}"
    external_url = f"https://t.me/{handle}"

    async with db.acquire() as conn:
        platform_id = await conn.fetchval(
            "INSERT INTO platforms (code, name) VALUES ($1, $2) RETURNING id",
            platform_code,
            "D7 ref-only platform",
        )
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            f"{_PREFIX}ref_acc_{suffix}",
        )
        channel_id = await conn.fetchval(
            "INSERT INTO source_channels (platform_id, external_channel_id, external_url, name) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            platform_id,
            handle,
            external_url,
            "D7 ref-only channel",
        )

    task = ClaimedTask(
        id=1,
        task_type_id=1,
        task_type_code="parser_add_channel",
        priority=500,
        payload={"parser_id": "p1", "channel_ref": channel_ref},
        channel_id=None,
        account_id=account_id,
        source_account_id=None,
        target_account_id=None,
        attempt_count=1,
        max_attempts=3,
        dedup_key=None,
        locked_by="test",
        locked_until=None,
    )
    clump = FakeClump()

    await sync_after_parser_add_channel(
        task,
        _account(account_id),
        clump,
    )

    assigned = await SourceChannelsRepo().get_assigned_account(channel_id)
    assert assigned == account_id

    async with db.acquire() as conn:
        await conn.execute(
            "DELETE FROM source_channels WHERE id = $1",
            channel_id,
        )
        await conn.execute(
            "DELETE FROM accounts WHERE id = $1",
            account_id,
        )
        await conn.execute(
            "DELETE FROM platforms WHERE id = $1",
            platform_id,
        )
