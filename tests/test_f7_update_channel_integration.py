"""F7 — integration: adapter-ветка update_channel на shared PG.

Проверяет, что после выполнения multi-op пайплайна update_channel:
- source_channels.last_updated_at обновлён (главный критерий F7);
- метаданные смёржены в source_channels.metadata (ключ extra_data);
- ресурс списан per-op в account_resource_usage.

Telethon не вызывается — клиент мокается фейком (как в unit-тестах F6/F7).
Задача создаётся сразу in_progress под локом теста, чтобы фоновый queue-worker
на общей БД её не перехватил.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.adapter import _execute_update_channel
from app_balance.queue.ops_catalog import TASK_TYPE_OPS, UPDATE_CHANNEL
from app_balance.queue.per_op_reading import TaskType, TaskTypesRepo
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.source_channels import SourceChannelsRepo
from app_balance.queue.task_queue import ClaimedTask, TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_f7_update_"
_TEST_PRIORITY = 2_000_000_000
_UPDATE_OP_CODES = [op.op_code for op in TASK_TYPE_OPS[UPDATE_CHANNEL]]


class _FakeEntity:
    def __init__(self) -> None:
        self.id = 1
        self.title = "F7 title"
        self.username = "f7user"
        self.megagroup = True
        self.participants_count = 7


class _FakeClient:
    def __init__(self) -> None:
        self.entity = _FakeEntity()

    async def get_entity(self, ref):
        return self.entity

    async def __call__(self, request):
        if type(request).__name__ == "GetFullChannelRequest":
            return type("F", (), {"full_chat": None})()
        return None

    def iter_messages(self, entity, limit):
        async def _gen():
            if False:
                yield None

        return _gen()

    async def get_participants(self, entity, limit):
        return []


async def _cleanup() -> None:
    async with db.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM account_resource_usage
            WHERE task_id IN (
                SELECT id FROM task_queue WHERE dedup_key LIKE $1
            )
            """,
            f"{_PREFIX}%",
        )
        await conn.execute(
            "DELETE FROM task_queue WHERE dedup_key LIKE $1",
            f"{_PREFIX}%",
        )
        await conn.execute(
            "DELETE FROM source_channels WHERE external_channel_id LIKE $1",
            f"{_PREFIX}%",
        )
        await conn.execute(
            "DELETE FROM platforms WHERE code LIKE $1",
            f"{_PREFIX}%",
        )
    await cleanup_queue_test_data(
        dedup_key_like=f"{_PREFIX}%",
        session_name_like=f"{_PREFIX}%",
    )


@pytest.fixture
async def f7_clean(pg_pool):
    await _cleanup()
    yield
    await _cleanup()


async def _insert_account() -> int:
    session_name = f"{_PREFIX}{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        return int(
            await conn.fetchval(
                "INSERT INTO accounts (session_name, status, is_enabled) "
                "VALUES ($1, 'active', true) RETURNING id",
                session_name,
            )
        )


async def _insert_channel(*, account_id: int) -> int:
    suffix = uuid.uuid4().hex
    async with db.acquire() as conn:
        platform_id = await conn.fetchval(
            "INSERT INTO platforms (code, name) VALUES ($1, $2) RETURNING id",
            f"{_PREFIX}{suffix}",
            "F7 test platform",
        )
        return int(
            await conn.fetchval(
                """
                INSERT INTO source_channels (
                    platform_id, external_channel_id, name, external_url,
                    assigned_account_id, last_updated_at
                ) VALUES ($1, $2, $3, $4, $5, NULL)
                RETURNING id
                """,
                platform_id,
                f"{_PREFIX}{suffix}",
                "old name",
                f"https://t.me/{_PREFIX}{suffix}",
                account_id,
            )
        )


async def _insert_in_progress_task(*, task_type: TaskType, channel_id: int, account_id: int) -> int:
    async with db.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                INSERT INTO task_queue (
                    task_type_id, task_type_code, status, priority,
                    channel_id, account_id, payload, dedup_key, max_attempts,
                    locked_by, locked_at, locked_until, run_after, started_at
                ) VALUES (
                    $1, $2, 'in_progress', $3,
                    $4, $5, '{}'::jsonb, $6, 5,
                    $7, now(), now() + interval '1 hour', now(), now()
                )
                RETURNING id
                """,
                task_type.id,
                UPDATE_CHANNEL,
                _TEST_PRIORITY,
                channel_id,
                account_id,
                f"{_PREFIX}{uuid.uuid4().hex}",
                f"{_PREFIX}lock",
            )
        )


def _claimed(*, task_id: int, task_type: TaskType, channel_id: int, account_id: int) -> ClaimedTask:
    return ClaimedTask(
        id=task_id,
        task_type_id=task_type.id,
        task_type_code=UPDATE_CHANNEL,
        priority=_TEST_PRIORITY,
        payload={},
        channel_id=channel_id,
        account_id=account_id,
        source_account_id=None,
        target_account_id=None,
        attempt_count=1,
        max_attempts=5,
        dedup_key=None,
        locked_by=f"{_PREFIX}lock",
        locked_until=None,
    )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_channel_sets_last_updated_at_and_metadata(f7_clean) -> None:
    task_type = await TaskTypesRepo().get_by_code(UPDATE_CHANNEL)
    if task_type is None:
        pytest.skip("update_channel отсутствует в seed")

    account_id = await _insert_account()
    channel_id = await _insert_channel(account_id=account_id)
    task_id = await _insert_in_progress_task(
        task_type=task_type, channel_id=channel_id, account_id=account_id
    )

    from app_balance.queue.accounts import AccountsRepo

    account = await AccountsRepo().get_by_id(account_id)
    assert account is not None

    client = _FakeClient()

    async def client_getter(session_name: str):
        return client

    await _execute_update_channel(
        _claimed(
            task_id=task_id,
            task_type=task_type,
            channel_id=channel_id,
            account_id=account_id,
        ),
        account=account,
        task_type=task_type,
        attempt_id=None,
        client_getter=client_getter,
        channels_repo=SourceChannelsRepo(),
        queue=TaskQueueRepo(),
        usage=ResourceUsageRepo(),
    )

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_updated_at, metadata, extra_data_collected "
            "FROM source_channels WHERE id = $1",
            channel_id,
        )
        usage_count = await conn.fetchval(
            "SELECT COUNT(*) FROM account_resource_usage WHERE task_id = $1",
            task_id,
        )

    assert row["last_updated_at"] is not None
    # F7 не трогает extra_data_collected (это зона F6).
    assert row["extra_data_collected"] is False
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    assert "extra_data" in metadata
    assert metadata["extra_data"]["title"] == "F7 title"
    # Per-op учёт: по записи на каждый enabled op пайплайна.
    assert int(usage_count) == len(_UPDATE_OP_CODES)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_save_channel_update_merges_metadata(f7_clean) -> None:
    account_id = await _insert_account()
    channel_id = await _insert_channel(account_id=account_id)

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE source_channels SET metadata = $2::jsonb WHERE id = $1",
            channel_id,
            json.dumps({"keep": "me"}),
        )

    ok = await SourceChannelsRepo().save_channel_update(
        channel_id, {"extra_data": {"title": "new"}}
    )
    assert ok is True

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_updated_at, metadata, name FROM source_channels WHERE id = $1",
            channel_id,
        )
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    assert metadata["keep"] == "me"
    assert metadata["extra_data"]["title"] == "new"
    assert row["last_updated_at"] is not None
    assert row["name"] == "new"
