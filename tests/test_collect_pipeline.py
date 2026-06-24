"""F6 — unit-тесты collect_pipeline (per-op сбор через фейковый Telethon-клиент)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app_balance.queue.collect_pipeline import (
    CollectContext,
    build_collect_op_executor,
    build_signals,
)
from app_balance.queue.errors import QueueTaskError
from app_balance.queue.ops_catalog import COLLECT_EXTRA_DATA, TASK_TYPE_OPS
from app_balance.queue.per_op_pipeline import ordered_pipeline
from app_balance.queue.per_op_reading import TaskType, TaskTypeOp

_COLLECT_OP_CODES = [op.op_code for op in TASK_TYPE_OPS[COLLECT_EXTRA_DATA]]


def _op(idx: int, code: str) -> TaskTypeOp:
    return TaskTypeOp(
        task_type_op_id=idx,
        op_type_id=idx,
        op_code=code,
        op_name=code,
        units_per_execution=1,
        account_role="primary",
        rph_limit=100,
        reserve_percent=Decimal("10"),
        op_is_enabled=True,
    )


def _collect_task_type() -> TaskType:
    ops = tuple(_op(i + 1, code) for i, code in enumerate(_COLLECT_OP_CODES))
    return TaskType(
        id=42,
        code=COLLECT_EXTRA_DATA,
        name=COLLECT_EXTRA_DATA,
        description=None,
        is_enabled=True,
        default_priority=200,
        min_available_resource_percent=90,
        requires_specific_account=False,
        uses_two_accounts=False,
        max_attempts=5,
        retry_delay_seconds=60,
        retry_backoff_multiplier=Decimal("2"),
        max_retry_delay_seconds=1800,
        target_queue_size=20,
        max_postpone_count=100,
        task_timeout_seconds=600,
        created_at=None,
        updated_at=None,
        ops=ops,
    )


class _FakeMessage:
    def __init__(self, views: int) -> None:
        self.views = views
        self.forwards = 1
        self.date = None
        self.action = None
        self.reactions = None
        self.replies = None


class _FakeUser:
    def __init__(self, uid: int, *, bot: bool = False, deleted: bool = False) -> None:
        self.id = uid
        self.bot = bot
        self.deleted = deleted


class _FakeFull:
    def __init__(self, participants_count: int, about: str) -> None:
        self.full_chat = type(
            "FC", (), {"participants_count": participants_count, "about": about}
        )()


class _FakeEntity:
    def __init__(self, *, megagroup: bool) -> None:
        self.id = 555
        self.title = "Test Channel"
        self.username = "testch"
        self.megagroup = megagroup
        self.participants_count = 1000


class _FakeClient:
    def __init__(self, entity: _FakeEntity) -> None:
        self.entity = entity
        self.requests: list[str] = []
        self.get_entity_calls = 0
        self.participants_calls = 0
        self._messages = [_FakeMessage(10), _FakeMessage(20)]
        self._participants = [
            _FakeUser(1),
            _FakeUser(2, bot=True),
            _FakeUser(3, deleted=True),
        ]

    async def get_entity(self, ref):
        self.get_entity_calls += 1
        return self.entity

    async def __call__(self, request):
        name = type(request).__name__
        self.requests.append(name)
        if name == "GetFullChannelRequest":
            return _FakeFull(participants_count=1234, about="about text")
        return None

    def iter_messages(self, entity, limit):
        messages = self._messages

        async def _gen():
            for m in messages:
                yield m

        return _gen()

    async def get_participants(self, entity, limit):
        self.participants_calls += 1
        return self._participants


async def _run_all_ops(client: _FakeClient, ctx: CollectContext) -> None:
    execute_op = build_collect_op_executor(client, "@testch", ctx)
    for step in ordered_pipeline(_collect_task_type()):
        await execute_op(step)


@pytest.mark.asyncio
async def test_full_pipeline_megagroup_collects_all() -> None:
    client = _FakeClient(_FakeEntity(megagroup=True))
    ctx = CollectContext()
    await _run_all_ops(client, ctx)

    assert client.get_entity_calls == 1
    assert "JoinChannelRequest" in client.requests
    assert "GetFullChannelRequest" in client.requests
    assert "LeaveChannelRequest" in client.requests
    assert ctx.joined is True
    assert ctx.left is True
    assert len(ctx.posts) == 2
    assert len(ctx.members) == 3
    assert client.participants_calls == 1


@pytest.mark.asyncio
async def test_get_participants_skipped_for_broadcast() -> None:
    client = _FakeClient(_FakeEntity(megagroup=False))
    ctx = CollectContext()
    await _run_all_ops(client, ctx)

    assert client.participants_calls == 0
    assert ctx.members == []


@pytest.mark.asyncio
async def test_build_signals_summarizes_context() -> None:
    client = _FakeClient(_FakeEntity(megagroup=True))
    ctx = CollectContext()
    await _run_all_ops(client, ctx)

    signals = build_signals(ctx)
    extra = signals["extra_data"]
    assert extra["title"] == "Test Channel"
    assert extra["username"] == "testch"
    assert extra["about"] == "about text"
    assert extra["participants_count"] == 1234  # из full_chat, не из entity
    assert extra["is_megagroup"] is True
    assert extra["posts_count"] == 2
    assert extra["members_sample"] == {"sampled": 3, "bots": 1, "deleted": 1}
    assert "collected_at" in extra


@pytest.mark.asyncio
async def test_telethon_error_mapped_to_typed_error() -> None:
    class _BoomClient(_FakeClient):
        async def get_entity(self, ref):
            raise RuntimeError("boom")

    client = _BoomClient(_FakeEntity(megagroup=True))
    ctx = CollectContext()
    execute_op = build_collect_op_executor(client, "@x", ctx)
    step = ordered_pipeline(_collect_task_type())[0]

    with pytest.raises(QueueTaskError):
        await execute_op(step)
