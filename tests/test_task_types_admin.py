"""T2–T5 — интеграционные тесты TaskTypesAdminRepo (task-types RPH API)."""
from __future__ import annotations

import pytest

from app_balance.queue import db
from app_balance.queue.ops_catalog import RESOURCE_OPS
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.primary_op import resolve_primary_op
from app_balance.queue.task_types_admin import (
    TaskTypePatchValidationError,
    TaskTypesAdminRepo,
)

from tests.conftest import requires_pg

MVP_CODES = frozenset(
    {
        "parser_add_channel",
        "parser_remove_channel",
        "move_channel",
        "collect_extra_data",
        "update_channel",
    }
)


@requires_pg
@pytest.mark.asyncio
async def test_list_all_contains_mvp_codes(pg_pool) -> None:
    repo = TaskTypesAdminRepo()
    views = await repo.list_all()
    codes = {v.code for v in views}
    assert MVP_CODES <= codes
    for view in views:
        assert view.rph.rph_limit_effective >= 1
        assert view.rph.rph_limit_default >= 1
        assert view.rph.primary_op_code


@requires_pg
@pytest.mark.asyncio
async def test_get_by_code_unknown(pg_pool) -> None:
    repo = TaskTypesAdminRepo()
    assert await repo.get_by_code("no_such_task_type_xyz") is None


@requires_pg
@pytest.mark.asyncio
async def test_patch_rph_roundtrip(pg_pool) -> None:
    repo = TaskTypesAdminRepo()
    code = "parser_add_channel"
    before = await repo.get_by_code(code)
    assert before is not None
    primary_op = before.rph.primary_op_code
    new_rph = before.rph.rph_limit_effective + 1

    updated = await repo.patch_rph(code, rph_limit=new_rph)
    assert updated.rph.rph_limit_effective == new_rph
    assert updated.rph.rph_auto_reduced is False

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT rph_limit FROM resource_op_types WHERE code = $1",
            primary_op,
        )
    assert row is not None
    assert int(row["rph_limit"]) == new_rph

    # restore
    await repo.patch_rph(code, rph_limit=before.rph.rph_limit_effective)


@requires_pg
@pytest.mark.asyncio
async def test_patch_updates_only_primary_op(pg_pool) -> None:
    repo = TaskTypesAdminRepo()
    task_types = TaskTypesRepo()
    task_type = await task_types.get_by_code("parser_add_channel")
    assert task_type is not None
    primary = resolve_primary_op(task_type)
    other_ops = [op for op in task_type.ops if op.op_code != primary.op_code]

    async with db.acquire() as conn:
        other_limits_before = {
            op.op_code: (
                await conn.fetchval(
                    "SELECT rph_limit FROM resource_op_types WHERE code = $1",
                    op.op_code,
                )
            )
            for op in other_ops
        }

    new_rph = primary.rph_limit + 5
    await repo.patch_rph("parser_add_channel", rph_limit=new_rph)

    async with db.acquire() as conn:
        for op_code, old_limit in other_limits_before.items():
            current = await conn.fetchval(
                "SELECT rph_limit FROM resource_op_types WHERE code = $1",
                op_code,
            )
            assert int(current) == int(old_limit)

    await repo.patch_rph("parser_add_channel", rph_limit=primary.rph_limit)


@requires_pg
@pytest.mark.asyncio
async def test_reset_after_g6_reduction(pg_pool) -> None:
    repo = TaskTypesAdminRepo()
    task_types = TaskTypesRepo()
    task_type = await task_types.get_by_code("parser_add_channel")
    assert task_type is not None
    primary = resolve_primary_op(task_type)
    default_rph = RESOURCE_OPS[primary.op_code].rph_limit
    reduced_rph = max(2, default_rph - 50)

    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE resource_op_types SET rph_limit = $2 WHERE id = $1",
            primary.op_type_id,
            reduced_rph,
        )
        await conn.execute(
            """
            INSERT INTO resource_limit_adjustments (
              error_code, op_code, op_type_id, action,
              old_rph_limit, new_rph_limit, account_id,
              error_count, window_seconds
            ) VALUES ('flood_wait', $1, $2, 'reduce_rph', $3, $4, NULL, 5, 3600)
            """,
            primary.op_code,
            primary.op_type_id,
            default_rph,
            reduced_rph,
        )

    view = await repo.get_by_code("parser_add_channel")
    assert view is not None
    assert view.rph.rph_limit_effective == reduced_rph
    assert view.rph.rph_auto_reduced is True
    assert view.rph.rph_reduced_at is not None

    reset = await repo.patch_rph("parser_add_channel", reset_rph_to_default=True)
    assert reset.rph.rph_limit_effective == default_rph
    assert reset.rph.rph_auto_reduced is False
    assert reset.rph.rph_reduced_at is None


@pytest.mark.asyncio
async def test_patch_validation_empty_body() -> None:
    repo = TaskTypesAdminRepo()
    with pytest.raises(TaskTypePatchValidationError):
        await repo.patch_rph("parser_add_channel")


@pytest.mark.asyncio
async def test_patch_validation_conflict() -> None:
    repo = TaskTypesAdminRepo()
    with pytest.raises(TaskTypePatchValidationError):
        await repo.patch_rph(
            "parser_add_channel",
            rph_limit=10,
            reset_rph_to_default=True,
        )
