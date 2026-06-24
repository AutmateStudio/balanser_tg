"""E7 — проверки согласованности ops_catalog ↔ seed ↔ docs."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app_balance.queue.ops_catalog import RESOURCE_OPS, TASK_TYPE_OPS
from app_balance.queue.ops_catalog_verify import (
    catalog_ops_list,
    effective_rph,
    parse_seed_resource_ops,
    parse_seed_task_type_ops,
    seed_sql,
    verify_catalog_internal,
    verify_seed_against_catalog,
)
from app_balance.queue.per_op_reading import TaskTypesRepo
from tests.conftest import requires_pg

_DOCS_OPS_CATALOG = Path(__file__).resolve().parents[1] / "docs" / "ops-catalog.md"


def test_catalog_internal_consistent() -> None:
    assert verify_catalog_internal() == []


def test_task_type_ops_use_known_resource_ops() -> None:
    resource_codes = set(RESOURCE_OPS.keys())
    task_ops_codes = {
        op.op_code
        for ops in TASK_TYPE_OPS.values()
        for op in ops
    }
    assert task_ops_codes.issubset(resource_codes)


def test_seed_matches_catalog() -> None:
    assert verify_seed_against_catalog() == []


def test_seed_resource_ops_cover_catalog() -> None:
    seed_resource_ops = parse_seed_resource_ops(seed_sql())
    assert set(seed_resource_ops.keys()) == set(RESOURCE_OPS.keys())
    for code, definition in RESOURCE_OPS.items():
        seed = seed_resource_ops[code]
        assert seed["rph_limit"] == definition.rph_limit
        assert seed["is_enabled"] == definition.is_enabled


def test_seed_task_type_ops_order_matches_catalog() -> None:
    seed_task_ops = parse_seed_task_type_ops(seed_sql())
    for task_type in TASK_TYPE_OPS:
        assert task_type in seed_task_ops
        assert seed_task_ops[task_type] == catalog_ops_list(task_type)


def test_effective_rph_formula() -> None:
    assert effective_rph(7) == 6
    assert effective_rph(80) == 72
    assert effective_rph(1) == 0
    assert effective_rph(100, reserve_percent=25) == 75


def test_docs_lists_all_resource_ops() -> None:
    """docs/ops-catalog.md упоминает каждый op-код из RESOURCE_OPS (нет дрейфа docs↔code)."""
    text = _DOCS_OPS_CATALOG.read_text(encoding="utf-8")
    missing = [code for code in RESOURCE_OPS if f"`{code}`" not in text]
    assert missing == [], f"В docs/ops-catalog.md нет op: {', '.join(missing)}"


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_type_ops_for_collect_update(pg_pool) -> None:
    repo = TaskTypesRepo()

    for task_type_code in ("collect_extra_data", "update_channel"):
        task_type = await repo.get_by_code(task_type_code)
        assert task_type is not None
        assert task_type.is_enabled is False
        ops = {
            (op.op_code, op.units_per_execution, op.account_role)
            for op in task_type.ops
        }
        assert ops == set(catalog_ops_list(task_type_code))
