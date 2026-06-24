"""F7 — unit-тесты adapter-ветки update_channel (_execute_update_channel)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app_balance.queue.accounts import Account
from app_balance.queue.adapter import _execute_update_channel, execute_task
from app_balance.queue.errors import PermanentError
from app_balance.queue.ops_catalog import TASK_TYPE_OPS, UPDATE_CHANNEL
from app_balance.queue.per_op_reading import TaskType, TaskTypeOp
from app_balance.queue.source_channels import CollectTarget
from app_balance.queue.task_queue import ClaimedTask

_UPDATE_OP_CODES = [op.op_code for op in TASK_TYPE_OPS[UPDATE_CHANNEL]]


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


def _update_task_type() -> TaskType:
    ops = tuple(_op(i + 1, code) for i, code in enumerate(_UPDATE_OP_CODES))
    return TaskType(
        id=43,
        code=UPDATE_CHANNEL,
        name=UPDATE_CHANNEL,
        description=None,
        is_enabled=True,
        default_priority=50,
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


def _claimed(*, channel_id: int | None) -> ClaimedTask:
    return ClaimedTask(
        id=8,
        task_type_id=43,
        task_type_code=UPDATE_CHANNEL,
        priority=50,
        payload={},
        channel_id=channel_id,
        account_id=99,
        source_account_id=None,
        target_account_id=None,
        attempt_count=1,
        max_attempts=5,
        dedup_key=None,
        locked_by="w",
        locked_until=None,
    )


def _account() -> Account:
    return Account(
        id=99,
        session_name="sess_99",
        status="active",
        is_enabled=True,
        current_task_id=8,
        cooldown_until=None,
        last_used_at=None,
    )


class _FakeEntity:
    def __init__(self) -> None:
        self.id = 1
        self.title = "T"
        self.username = "u"
        self.megagroup = True
        self.participants_count = 5


class _FakeClient:
    def __init__(self) -> None:
        self.ops: list[str] = []
        self.entity = _FakeEntity()

    async def get_entity(self, ref):
        self.ops.append("get_entity")
        return self.entity

    async def __call__(self, request):
        self.ops.append(type(request).__name__)
        if type(request).__name__ == "GetFullChannelRequest":
            return type("F", (), {"full_chat": None})()
        return None

    def iter_messages(self, entity, limit):
        self.ops.append("iter_messages")

        async def _gen():
            if False:
                yield None

        return _gen()

    async def get_participants(self, entity, limit):
        self.ops.append("get_participants")
        return []


class _FakeQueue:
    def __init__(self) -> None:
        self.steps: list[str] = []

    async def set_last_completed_step(self, task_id: int, step: str) -> None:
        self.steps.append(step)


class _FakeUsage:
    def __init__(self) -> None:
        self.records: list[str] = []

    async def record_op(self, *, task_type_id, task_id, op, account_id, task_attempt_id=None):
        self.records.append(op.op_code)
        return len(self.records)


class _FakeChannels:
    def __init__(self, target: CollectTarget | None) -> None:
        self._target = target
        self.updated: list[tuple[int, dict]] = []
        self.saved_extra: list[tuple[int, dict]] = []

    async def get_collect_target(self, channel_id: int):
        return self._target

    async def save_channel_update(self, channel_id: int, signals: dict) -> bool:
        self.updated.append((channel_id, signals))
        return True

    async def save_extra_data(self, channel_id: int, signals: dict) -> bool:
        self.saved_extra.append((channel_id, signals))
        return True


@pytest.mark.asyncio
async def test_update_runs_all_ops_and_saves_update() -> None:
    client = _FakeClient()
    queue = _FakeQueue()
    usage = _FakeUsage()
    channels = _FakeChannels(
        CollectTarget(id=20, external_url="https://t.me/upch", external_channel_id="-200")
    )

    async def client_getter(session_name: str):
        return client

    await _execute_update_channel(
        _claimed(channel_id=20),
        account=_account(),
        task_type=_update_task_type(),
        attempt_id=3,
        client_getter=client_getter,
        channels_repo=channels,
        queue=queue,
        usage=usage,
    )

    # Все op пайплайна списали ресурс и зафиксировали прогресс.
    assert usage.records == _UPDATE_OP_CODES
    assert queue.steps == _UPDATE_OP_CODES
    # save_channel_update вызван (а save_extra_data — нет: флаг не трогаем).
    assert len(channels.updated) == 1
    assert channels.saved_extra == []
    saved_channel_id, signals = channels.updated[0]
    assert saved_channel_id == 20
    assert "extra_data" in signals


@pytest.mark.asyncio
async def test_update_missing_channel_id_is_permanent() -> None:
    channels = _FakeChannels(None)

    async def client_getter(session_name: str):
        return _FakeClient()

    with pytest.raises(PermanentError):
        await _execute_update_channel(
            _claimed(channel_id=None),
            account=_account(),
            task_type=_update_task_type(),
            attempt_id=None,
            client_getter=client_getter,
            channels_repo=channels,
            queue=_FakeQueue(),
            usage=_FakeUsage(),
        )


@pytest.mark.asyncio
async def test_update_channel_not_found_is_permanent() -> None:
    channels = _FakeChannels(None)

    async def client_getter(session_name: str):
        return _FakeClient()

    with pytest.raises(PermanentError):
        await _execute_update_channel(
            _claimed(channel_id=20),
            account=_account(),
            task_type=_update_task_type(),
            attempt_id=None,
            client_getter=client_getter,
            channels_repo=channels,
            queue=_FakeQueue(),
            usage=_FakeUsage(),
        )


@pytest.mark.asyncio
async def test_execute_task_update_requires_task_type() -> None:
    with pytest.raises(PermanentError):
        await execute_task(
            _claimed(channel_id=20),
            account=_account(),
            task_type=None,
        )
