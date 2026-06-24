"""F6 — unit-тест развязки учёта ресурса в dispatch для multi-op типов.

collect_extra_data (multi-op): dispatch НЕ вызывает record_for_task (учёт ведёт
adapter пошагово). parser_add_channel (single-op): record_for_task вызывается.
"""
from __future__ import annotations

import pytest

from app_balance.queue.dispatch import DispatchResult, TaskDispatcher
from app_balance.queue.ops_catalog import COLLECT_EXTRA_DATA
from tests.test_dispatch import (
    FakeAccounts,
    FakeAttempts,
    FakeResourceChecker,
    _claimed,
    _fake_queue,
    _task_type,
)


class _FakeUsage:
    def __init__(self) -> None:
        self.record_for_task_calls: list[dict] = []

    async def record_for_task(self, **kwargs):
        self.record_for_task_calls.append(kwargs)
        return []


class _FakeTaskTypes:
    def __init__(self, task_type) -> None:
        self._task_type = task_type

    async def get_by_code(self, code: str):
        return self._task_type


class _CapturingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, int | None]] = []

    async def execute(self, task, *, account, task_type=None, attempt_id=None) -> None:
        self.calls.append((task.task_type_code, task_type is not None, attempt_id))


def _build(task_type, usage, adapter) -> TaskDispatcher:
    return TaskDispatcher(
        queue=_fake_queue(),
        accounts=FakeAccounts(),
        task_types=_FakeTaskTypes(task_type),
        adapter=adapter,
        resource_check=FakeResourceChecker(),
        usage=usage,
        attempts=FakeAttempts(),
    )


@pytest.mark.asyncio
async def test_multi_op_skips_record_for_task() -> None:
    task_type = _task_type(code=COLLECT_EXTRA_DATA)
    usage = _FakeUsage()
    adapter = _CapturingAdapter()

    result = await _build(task_type, usage, adapter).dispatch(
        _claimed(task_type_code=COLLECT_EXTRA_DATA)
    )

    assert result == DispatchResult.COMPLETED
    # record_for_task НЕ вызван для multi-op типа.
    assert usage.record_for_task_calls == []
    # adapter получил task_type и attempt_id (для пошагового учёта).
    assert adapter.calls == [(COLLECT_EXTRA_DATA, True, 1)]


@pytest.mark.asyncio
async def test_single_op_calls_record_for_task() -> None:
    task_type = _task_type(code="parser_add_channel")
    usage = _FakeUsage()
    adapter = _CapturingAdapter()

    result = await _build(task_type, usage, adapter).dispatch(
        _claimed(task_type_code="parser_add_channel")
    )

    assert result == DispatchResult.COMPLETED
    # record_for_task вызван ровно один раз для single-op типа.
    assert len(usage.record_for_task_calls) == 1
    # adapter вызван без task_type (single-call путь).
    assert adapter.calls == [("parser_add_channel", False, None)]
