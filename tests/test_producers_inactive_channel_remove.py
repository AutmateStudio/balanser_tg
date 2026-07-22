"""Unit-тесты InactiveChannelRemoveProducer / list_inactive_on_sessions (без PG/Telethon)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app_balance.queue.per_op_reading import TaskType
from app_balance.queue.producers.base import ProduceResult
from app_balance.queue.producers.inactive_channel_remove import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_STALE_AFTER_SECONDS,
    InactiveChannelRemoveProducer,
    SessionLocation,
    find_on_session,
    resolve_owner_from_job,
)
from app_balance.queue.source_channels import (
    InactiveOnSessionChannel,
    _LIST_INACTIVE_ON_SESSIONS_SQL,
)
from app_balance.queue.task_queue import EnqueueInput, EnqueueResult


def _task_type(
    *,
    code: str = "parser_remove_channel",
    target_queue_size: int | None = 20,
    is_enabled: bool = True,
) -> TaskType:
    return TaskType(
        id=5,
        code=code,
        name=code,
        description=None,
        is_enabled=is_enabled,
        default_priority=400,
        min_available_resource_percent=90,
        requires_specific_account=False,
        uses_two_accounts=False,
        max_attempts=5,
        retry_delay_seconds=60,
        retry_backoff_multiplier=Decimal("2"),
        max_retry_delay_seconds=1800,
        target_queue_size=target_queue_size,
        max_postpone_count=100,
        task_timeout_seconds=600,
        created_at=None,
        updated_at=None,
        ops=(),
    )


def _channel(
    *,
    channel_id: int = 10,
    account_id: int = 100,
    session_name: str = "acc1",
    external_url: str | None = "https://t.me/demo",
    external_channel_id: str | None = None,
) -> InactiveOnSessionChannel:
    return InactiveOnSessionChannel(
        channel_id=channel_id,
        account_id=account_id,
        session_name=session_name,
        external_url=external_url,
        external_channel_id=external_channel_id,
        activity_at=None,
    )


def test_inactive_select_sql_predicates() -> None:
    sql = _LIST_INACTIVE_ON_SESSIONS_SQL
    assert "assigned_account_id IS NOT NULL" in sql
    assert "monitoring_projects" in sql
    assert "source_messages" in sql
    assert "platform_id" in sql or "platforms" in sql
    assert "ORDER BY COALESCE" in sql


@pytest.mark.asyncio
async def test_list_inactive_on_sessions_queries_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app_balance.queue.source_channels import SourceChannelsRepo

    conn = AsyncMock()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": 10,
                "assigned_account_id": 100,
                "external_url": "https://t.me/a",
                "external_channel_id": None,
                "session_name": "acc1",
                "activity_at": None,
            }
        ]
    )

    @asynccontextmanager
    async def fake_acquire():
        yield conn

    monkeypatch.setattr(
        "app_balance.queue.source_channels.acquire", fake_acquire
    )

    repo = SourceChannelsRepo()
    result = await repo.list_inactive_on_sessions(
        limit=5, stale_after_seconds=100
    )

    assert result == [
        InactiveOnSessionChannel(
            channel_id=10,
            account_id=100,
            session_name="acc1",
            external_url="https://t.me/a",
            external_channel_id=None,
            activity_at=None,
        )
    ]
    conn.fetch.assert_awaited_once_with(_LIST_INACTIVE_ON_SESSIONS_SQL, 100, 5)


def test_inactive_on_session_channel_ref() -> None:
    assert _channel().ref() == "https://t.me/demo"
    assert (
        InactiveOnSessionChannel(
            channel_id=1,
            account_id=1,
            session_name="s",
            external_url="  ",
            external_channel_id="@foo",
            activity_at=None,
        ).ref()
        == "@foo"
    )


def test_resolve_owner_from_job_assignments() -> None:
    job = {
        "parser_id": "p1",
        "assignments": {"https://t.me/demo": "acc1"},
        "channel_list": ["https://t.me/demo"],
        "session_name_list": ["acc1"],
    }
    assert resolve_owner_from_job(job, "https://t.me/demo") == "acc1"
    assert resolve_owner_from_job(job, "demo") == "acc1"


def test_resolve_owner_from_job_channel_list_single_session() -> None:
    job = {
        "parser_id": "p1",
        "assignments": {},
        "channel_list": ["https://t.me/demo"],
        "session_name_list": ["acc1"],
    }
    assert resolve_owner_from_job(job, "demo") == "acc1"


def test_resolve_owner_from_job_ambiguous_sessions() -> None:
    job = {
        "parser_id": "p1",
        "assignments": {},
        "channel_list": ["https://t.me/demo"],
        "session_name_list": ["acc1", "acc2"],
    }
    assert resolve_owner_from_job(job, "demo") is None
    assert resolve_owner_from_job(job, "demo", preferred_session="acc1") == "acc1"


def test_find_on_session_prefers_jobs_when_no_clumps() -> None:
    jobs = [
        {
            "parser_id": "p1",
            "assignments": {"https://t.me/demo": "acc1"},
            "channel_list": [],
            "session_name_list": ["acc1"],
        }
    ]
    loc = find_on_session("https://t.me/demo", clumps=[], jobs=jobs)
    assert loc == SessionLocation(parser_id="p1", session_name="acc1")


def test_producer_defaults() -> None:
    assert DEFAULT_STALE_AFTER_SECONDS == 2_592_000
    assert DEFAULT_BATCH_SIZE > 0


@pytest.mark.asyncio
async def test_produce_returns_empty_when_type_disabled() -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_task_type(is_enabled=False))
    channels = AsyncMock()
    channels.list_inactive_on_sessions = AsyncMock()

    producer = InactiveChannelRemoveProducer(
        task_types=task_types,
        channels=channels,
        load_clumps=lambda: [],
        load_jobs=lambda: [],
    )
    result = await producer.produce()

    assert result == []
    channels.list_inactive_on_sessions.assert_not_awaited()


@pytest.mark.asyncio
async def test_produce_skips_when_not_on_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_task_type())
    channels = AsyncMock()
    channels.list_inactive_on_sessions = AsyncMock(return_value=[_channel()])
    channels.clear_assigned_account = AsyncMock(return_value=True)
    task_queue = AsyncMock()

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=0),
    )

    # Пустой store: не чистим assigned (нельзя отличить «нет канала» от «store недоступен»).
    producer = InactiveChannelRemoveProducer(
        task_queue=task_queue,
        task_types=task_types,
        channels=channels,
        clear_orphan_assigned=True,
        load_clumps=lambda: [],
        load_jobs=lambda: [],
    )
    result = await producer.produce()

    assert result == []
    task_queue.enqueue.assert_not_awaited()
    channels.clear_assigned_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_produce_clears_orphan_when_store_has_other_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_task_type())
    channels = AsyncMock()
    channels.list_inactive_on_sessions = AsyncMock(return_value=[_channel()])
    channels.clear_assigned_account = AsyncMock(return_value=True)
    task_queue = AsyncMock()

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=0),
    )

    jobs = [
        {
            "parser_id": "p1",
            "assignments": {"https://t.me/other": "acc9"},
            "channel_list": ["https://t.me/other"],
            "session_name_list": ["acc9"],
        }
    ]
    producer = InactiveChannelRemoveProducer(
        task_queue=task_queue,
        task_types=task_types,
        channels=channels,
        clear_orphan_assigned=True,
        load_clumps=lambda: [],
        load_jobs=lambda: jobs,
    )
    result = await producer.produce()

    assert result == []
    task_queue.enqueue.assert_not_awaited()
    channels.clear_assigned_account.assert_awaited_once_with(10)


@pytest.mark.asyncio
async def test_produce_enqueues_preferred_session_in_multi_session_clump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_task_type())
    channels = AsyncMock()
    channels.list_inactive_on_sessions = AsyncMock(return_value=[_channel()])
    task_queue = AsyncMock()
    task_queue.enqueue = AsyncMock(
        return_value=EnqueueResult(created=True, task_id=42)
    )

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=0),
    )

    jobs = [
        {
            "parser_id": "p1",
            "assignments": {},
            "channel_list": ["https://t.me/demo"],
            "session_name_list": ["acc1", "acc2"],
        }
    ]
    producer = InactiveChannelRemoveProducer(
        task_queue=task_queue,
        task_types=task_types,
        channels=channels,
        load_clumps=lambda: [],
        load_jobs=lambda: jobs,
    )
    result = await producer.produce()

    assert result == [ProduceResult(created=True, task_id=42)]
    task_queue.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_produce_enqueues_case_insensitive_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_task_type())
    channels = AsyncMock()
    channels.list_inactive_on_sessions = AsyncMock(
        return_value=[_channel(session_name="acc1")]
    )
    task_queue = AsyncMock()
    task_queue.enqueue = AsyncMock(
        return_value=EnqueueResult(created=True, task_id=7)
    )

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=0),
    )

    jobs = [
        {
            "parser_id": "p1",
            "assignments": {"https://t.me/demo": "Acc1"},
            "channel_list": ["https://t.me/demo"],
            "session_name_list": ["Acc1"],
        }
    ]
    producer = InactiveChannelRemoveProducer(
        task_queue=task_queue,
        task_types=task_types,
        channels=channels,
        load_clumps=lambda: [],
        load_jobs=lambda: jobs,
    )
    result = await producer.produce()

    assert result == [ProduceResult(created=True, task_id=7)]
    task_queue.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_produce_enqueues_when_in_assignments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_task_type(target_queue_size=20))
    channels = AsyncMock()
    channels.list_inactive_on_sessions = AsyncMock(return_value=[_channel()])
    task_queue = AsyncMock()
    task_queue.enqueue = AsyncMock(
        return_value=EnqueueResult(created=True, task_id=501)
    )

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=5),
    )

    jobs = [
        {
            "parser_id": "p1",
            "assignments": {"https://t.me/demo": "acc1"},
            "channel_list": ["https://t.me/demo"],
            "session_name_list": ["acc1"],
        }
    ]
    producer = InactiveChannelRemoveProducer(
        task_queue=task_queue,
        task_types=task_types,
        channels=channels,
        stale_after_seconds=12345,
        load_clumps=lambda: [],
        load_jobs=lambda: jobs,
    )
    result = await producer.produce()

    assert result == [ProduceResult(created=True, task_id=501)]
    channels.list_inactive_on_sessions.assert_awaited_once_with(
        limit=15, stale_after_seconds=12345
    )
    call: EnqueueInput = task_queue.enqueue.await_args.args[0]
    assert call.task_type_code == "parser_remove_channel"
    assert call.channel_id == 10
    assert call.account_id == 100
    assert call.dedup_key == "parser_remove_channel:p1:demo"
    assert call.created_by == "inactive_channel_remove_producer"
    assert call.payload == {"parser_id": "p1", "channel_ref": "https://t.me/demo"}


def test_dedup_key_matches_d9_for_private_c_link() -> None:
    from app_balance.queue.producers.inactive_channel_remove import _dedup_key
    from discovery_api.parser_functions import _normalize_channel_ref

    raw = "https://t.me/c/1234567890"
    assert _dedup_key("p1", raw) == (
        f"parser_remove_channel:p1:{_normalize_channel_ref(raw)}"
    )


@pytest.mark.asyncio
async def test_produce_skips_session_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(return_value=_task_type())
    channels = AsyncMock()
    channels.list_inactive_on_sessions = AsyncMock(
        return_value=[_channel(session_name="pg_acc")]
    )
    task_queue = AsyncMock()

    monkeypatch.setattr(
        "app_balance.queue.producers.base.count_active_tasks",
        AsyncMock(return_value=0),
    )

    jobs = [
        {
            "parser_id": "p1",
            "assignments": {"https://t.me/demo": "store_acc"},
            "channel_list": ["https://t.me/demo"],
            "session_name_list": ["store_acc"],
        }
    ]
    producer = InactiveChannelRemoveProducer(
        task_queue=task_queue,
        task_types=task_types,
        channels=channels,
        load_clumps=lambda: [],
        load_jobs=lambda: jobs,
    )
    result = await producer.produce()

    assert result == []
    task_queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_produce_uses_default_batch_when_unlimited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_types = AsyncMock()
    task_types.get_by_code = AsyncMock(
        return_value=_task_type(target_queue_size=None)
    )
    channels = AsyncMock()
    channels.list_inactive_on_sessions = AsyncMock(return_value=[])

    producer = InactiveChannelRemoveProducer(
        task_types=task_types,
        channels=channels,
        load_clumps=lambda: [],
        load_jobs=lambda: [],
    )
    result = await producer.produce()

    assert result == []
    channels.list_inactive_on_sessions.assert_awaited_once_with(
        limit=DEFAULT_BATCH_SIZE,
        stale_after_seconds=DEFAULT_STALE_AFTER_SECONDS,
    )
