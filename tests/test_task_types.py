"""B2 — интеграционные тесты TaskTypesRepo."""
from __future__ import annotations

import pytest

from app_balance.queue.per_op_reading import TaskTypesRepo
from tests.conftest import requires_pg


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_by_code_parser_add_channel(pg_pool) -> None:
    repo = TaskTypesRepo()
    task_type = await repo.get_by_code("parser_add_channel")

    assert task_type is not None
    assert task_type.is_enabled is True
    assert task_type.default_priority == 500
    assert task_type.min_available_resource_percent == 80
    assert task_type.uses_two_accounts is False
    assert len(task_type.ops) == 4
    assert all(op.account_role == "primary" for op in task_type.ops)
    op_codes = {op.op_code for op in task_type.ops}
    assert op_codes == {
        "get_entity",
        "channels.JoinChannel",
        "channels.GetFullChannel",
        "channels.GetParticipant",
    }


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_by_code_move_channel_dual_roles(pg_pool) -> None:
    repo = TaskTypesRepo()
    task_type = await repo.get_by_code("move_channel")

    assert task_type is not None
    assert task_type.uses_two_accounts is True
    roles = {op.account_role for op in task_type.ops}
    assert roles == {"source", "target"}


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_enabled(pg_pool) -> None:
    repo = TaskTypesRepo()
    enabled = await repo.list_enabled()
    codes = {item.code for item in enabled}

    # Канон A9_seed: add, move, remove (D9), update (F7); collect — off до F6.
    assert codes == {
        "parser_add_channel",
        "move_channel",
        "parser_remove_channel",
        "update_channel",
    }
    assert "collect_extra_data" not in codes
    assert all(item.is_enabled for item in enabled)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_by_code_not_found(pg_pool) -> None:
    repo = TaskTypesRepo()
    assert await repo.get_by_code("no_such_task_type_xyz") is None
