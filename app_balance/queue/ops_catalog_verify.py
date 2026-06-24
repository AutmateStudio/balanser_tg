"""E7 — общая логика сверки ops_catalog ↔ seed ↔ БД.

Единый источник правил сверки для CLI (`scripts/verify_ops_catalog_seed.py`)
и тестов (`tests/test_ops_catalog.py`), чтобы regex и проверки не дублировались.
"""
from __future__ import annotations

import re
from math import floor
from pathlib import Path

from app_balance.queue.ops_catalog import RESOURCE_OPS, TASK_TYPE_OPS

# reserve_percent по умолчанию (DB/A9_seed.sql, schema resource_op_types).
DEFAULT_RESERVE_PERCENT = 10

SEED_PATH = Path(__file__).resolve().parents[2] / "DB" / "A9_seed.sql"

_RESOURCE_BLOCK_RE = re.compile(
    r"INSERT INTO resource_op_types.*?ON CONFLICT",
    re.S,
)
_TASK_OP_BLOCK_RE = re.compile(
    r"INSERT INTO task_type_ops.*?JOIN \(VALUES(?P<values>.*?)\)\s+AS v"
    r"\(op_code, units, role\).*?WHERE tt.code = '(?P<task_type>[^']+)'",
    re.S,
)
_RESOURCE_ROW_RE = re.compile(
    r"\('([^']+)'\s*,\s*'[^']*'\s*,\s*(\d+)\s*,\s*(true|false)\)",
    re.I,
)
_TASK_OP_ROW_RE = re.compile(r"\('([^']+)'\s*,\s*(\d+)\s*,\s*'([^']+)'\)")


def effective_rph(rph_limit: int, reserve_percent: int = DEFAULT_RESERVE_PERCENT) -> int:
    """effective_rph = floor(rph_limit × (1 − reserve_percent/100))."""
    return floor(rph_limit * (1 - reserve_percent / 100))


def seed_sql(path: Path | None = None) -> str:
    return (path or SEED_PATH).read_text(encoding="utf-8")


def parse_seed_resource_ops(sql: str) -> dict[str, dict[str, object]]:
    match = _RESOURCE_BLOCK_RE.search(sql)
    if not match:
        raise ValueError("Не найден INSERT INTO resource_op_types в A9_seed.sql")
    block = match.group(0)
    result: dict[str, dict[str, object]] = {}
    for code, rph, enabled in _RESOURCE_ROW_RE.findall(block):
        result[code] = {
            "rph_limit": int(rph),
            "is_enabled": enabled.lower() == "true",
        }
    return result


def parse_seed_task_type_ops(sql: str) -> dict[str, list[tuple[str, int, str]]]:
    """Возвращает op-состав по task type в ПОРЯДКЕ объявления в seed."""
    result: dict[str, list[tuple[str, int, str]]] = {}
    for match in _TASK_OP_BLOCK_RE.finditer(sql):
        task_type = match.group("task_type")
        values = match.group("values")
        ops: list[tuple[str, int, str]] = []
        for op_code, units, role in _TASK_OP_ROW_RE.findall(values):
            ops.append((op_code, int(units), role))
        result[task_type] = ops
    return result


def catalog_ops_list(task_type: str) -> list[tuple[str, int, str]]:
    """op-состав task type из каталога в порядке pipeline."""
    return [
        (op.op_code, op.units_per_execution, op.account_role)
        for op in TASK_TYPE_OPS[task_type]
    ]


def verify_catalog_internal() -> list[str]:
    """Внутренняя согласованность каталога: op-коды pipeline ∈ RESOURCE_OPS, без повторов."""
    issues: list[str] = []
    resource_codes = set(RESOURCE_OPS.keys())
    for task_type, ops in TASK_TYPE_OPS.items():
        seen: set[tuple[str, str]] = set()
        for op in ops:
            if op.op_code not in resource_codes:
                issues.append(
                    f"Pipeline {task_type}: op {op.op_code} отсутствует в RESOURCE_OPS"
                )
            key = (op.op_code, op.account_role)
            if key in seen:
                issues.append(
                    f"Pipeline {task_type}: дубль op {op.op_code} (role={op.account_role})"
                )
            seen.add(key)
    return issues


def verify_seed_against_catalog(sql: str | None = None) -> list[str]:
    """Сверяет A9_seed.sql с каталогом: коды, RPH, is_enabled, состав и ПОРЯДОК ops."""
    issues: list[str] = list(verify_catalog_internal())
    text = sql if sql is not None else seed_sql()
    seed_resource_ops = parse_seed_resource_ops(text)
    seed_task_ops = parse_seed_task_type_ops(text)

    seed_codes = set(seed_resource_ops.keys())
    catalog_codes = set(RESOURCE_OPS.keys())
    if seed_codes != catalog_codes:
        extra = sorted(seed_codes - catalog_codes)
        missing = sorted(catalog_codes - seed_codes)
        if extra:
            issues.append(f"Seed содержит лишние op: {', '.join(extra)}")
        if missing:
            issues.append(f"Seed не содержит op: {', '.join(missing)}")

    for code, definition in RESOURCE_OPS.items():
        seed = seed_resource_ops.get(code)
        if seed is None:
            continue
        if seed["rph_limit"] != definition.rph_limit:
            issues.append(
                f"Seed rph_limit для {code}={seed['rph_limit']} "
                f"≠ catalog {definition.rph_limit}"
            )
        if seed["is_enabled"] != definition.is_enabled:
            issues.append(
                f"Seed is_enabled для {code}={seed['is_enabled']} "
                f"≠ catalog {definition.is_enabled}"
            )

    for task_type in TASK_TYPE_OPS:
        seed_ops = seed_task_ops.get(task_type)
        if seed_ops is None:
            issues.append(f"Seed не содержит task_type_ops для {task_type}")
            continue
        expected_ops = catalog_ops_list(task_type)
        if set(seed_ops) != set(expected_ops):
            issues.append(
                f"Seed task_type_ops для {task_type} не совпадает с catalog (состав)"
            )
        elif seed_ops != expected_ops:
            issues.append(
                f"Seed task_type_ops для {task_type} не совпадает с catalog (порядок)"
            )

    return issues


async def verify_db_against_catalog() -> list[str]:
    """Сверяет живую БД (resource_op_types + task_type_ops) с каталогом.

    Требует инициализированного пула (db.init_pool). Порядок ops в БД не
    гарантируется, поэтому сверяется состав (set), не порядок.
    """
    from app_balance.queue import db
    from app_balance.queue.per_op_reading import TaskTypesRepo

    issues: list[str] = []
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT code, rph_limit, is_enabled FROM resource_op_types"
        )
    db_ops = {row["code"]: row for row in rows}

    db_codes = set(db_ops.keys())
    catalog_codes = set(RESOURCE_OPS.keys())
    if db_codes != catalog_codes:
        extra = sorted(db_codes - catalog_codes)
        missing = sorted(catalog_codes - db_codes)
        if extra:
            issues.append(f"БД содержит лишние op: {', '.join(extra)}")
        if missing:
            issues.append(f"БД не содержит op: {', '.join(missing)}")

    for code, definition in RESOURCE_OPS.items():
        row = db_ops.get(code)
        if row is None:
            continue
        if row["rph_limit"] != definition.rph_limit:
            issues.append(
                f"БД rph_limit для {code}={row['rph_limit']} "
                f"≠ catalog {definition.rph_limit}"
            )
        if row["is_enabled"] != definition.is_enabled:
            issues.append(
                f"БД is_enabled для {code}={row['is_enabled']} "
                f"≠ catalog {definition.is_enabled}"
            )

    repo = TaskTypesRepo()
    for task_type in TASK_TYPE_OPS:
        task = await repo.get_by_code(task_type)
        if task is None:
            issues.append(f"БД не содержит task_type {task_type}")
            continue
        db_ops_set = {
            (op.op_code, op.units_per_execution, op.account_role)
            for op in task.ops
        }
        expected_ops = set(catalog_ops_list(task_type))
        if db_ops_set != expected_ops:
            issues.append(
                f"БД task_type_ops для {task_type} не совпадает с catalog"
            )

    return issues
