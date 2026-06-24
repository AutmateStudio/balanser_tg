"""E8 — приёмочный тест идемпотентности retry многошаговой задачи (ТЗ §29).

После сбоя на середине пайплайна повтор продолжает с упавшего op и НЕ дублирует
уже выполненные шаги: повторный INSERT в account_resource_usage делается только
для оставшихся op.

Контракт E6, который проверяет этот тест (шаг = op-код из ops_catalog.py):
- payload.last_completed_step хранит код последнего успешно выполненного op;
- retry стартует со следующего op после last_completed_step;
- ресурс (account_resource_usage) списывается только за новые op.

Примечания:
- E6 ещё не вшит в TaskDispatcher (он списывает все op разом до execute), поэтому
  тест моделирует per-op поведение будущего adapter F6 для collect_extra_data
  напрямую — через ResourceUsageRepo + payload.last_completed_step.
- Задача создаётся сразу в in_progress под локом теста (locked_until в будущем) и
  не возвращается в пул, чтобы фоновый queue-worker на общей БД не перехватил её
  между попытками. collect_extra_data остаётся выключенным (is_enabled=false).
"""
from __future__ import annotations

import json
import uuid

import pytest

from app_balance.queue import db
from app_balance.queue.error_codes import ErrorCode
from app_balance.queue.errors import RetryableError
from app_balance.queue.ops_catalog import COLLECT_EXTRA_DATA, TASK_TYPE_OPS
from app_balance.queue.per_op_reading import TaskType, TaskTypesRepo
from app_balance.queue.resource_usage import ResourceUsageRepo
from app_balance.queue.task_queue import TaskQueueRepo
from tests.conftest import requires_pg
from tests.pg_cleanup import cleanup_queue_test_data

_PREFIX = "test_e8_"
_TEST_PRIORITY = 2_000_000_000
_FAIL_AT_INDEX = 2  # упасть на 3-м op пайплайна


@pytest.fixture
async def e8_clean(pg_pool):
    async def _cleanup() -> None:
        await cleanup_queue_test_data(
            dedup_key_like=f"{_PREFIX}%",
            session_name_like=f"{_PREFIX}%",
        )

    await _cleanup()
    yield
    await _cleanup()


async def _insert_account() -> tuple[int, str]:
    session_name = f"{_PREFIX}{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            session_name,
        )
    return int(account_id), session_name


async def _insert_in_progress_task(*, task_type: TaskType, account_id: int) -> int:
    """Создаёт задачу сразу в in_progress под локом теста (не отдаём в пул воркера)."""
    async with db.acquire() as conn:
        task_id = await conn.fetchval(
            """
            INSERT INTO task_queue (
                task_type_id, task_type_code, status, priority,
                account_id, payload, dedup_key, max_attempts,
                locked_by, locked_at, locked_until, run_after, started_at
            ) VALUES (
                $1, $2, 'in_progress', $3,
                $4, $5::jsonb, $6, $7,
                $8, now(), now() + interval '1 hour', now(), now()
            )
            RETURNING id
            """,
            task_type.id,
            task_type.code,
            _TEST_PRIORITY,
            account_id,
            json.dumps({"ref": "@e8_test"}),
            f"{_PREFIX}{uuid.uuid4().hex}",
            task_type.max_attempts,
            f"{_PREFIX}lock",
        )
    return int(task_id)


async def _get_payload(task_id: int) -> dict:
    async with db.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT payload FROM task_queue WHERE id = $1", task_id
        )
    if isinstance(raw, str):
        return json.loads(raw) or {}
    return dict(raw or {})


async def _set_payload(task_id: int, payload: dict) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET payload = $2::jsonb WHERE id = $1",
            task_id,
            json.dumps(payload),
        )


async def _usage_rows(task_id: int) -> list:
    async with db.acquire() as conn:
        return list(
            await conn.fetch(
                "SELECT op_type_id, units FROM account_resource_usage "
                "WHERE task_id = $1 ORDER BY id",
                task_id,
            )
        )


async def _run_collect_pipeline(
    *,
    task_id: int,
    account_id: int,
    task_type: TaskType,
    op_type_id_by_code: dict[str, int],
    fail_at_index: int | None,
) -> None:
    """Моделирует per-op adapter F6 с идемпотентностью E6.

    Списывает ресурс и продвигает payload.last_completed_step после каждого
    успешного op. Стартует со следующего op после last_completed_step.
    """
    usage = ResourceUsageRepo()
    pipeline = TASK_TYPE_OPS[COLLECT_EXTRA_DATA]
    codes = [op.op_code for op in pipeline]

    payload = await _get_payload(task_id)
    last_step = payload.get("last_completed_step")
    start = codes.index(last_step) + 1 if last_step in codes else 0

    for index in range(start, len(pipeline)):
        op = pipeline[index]
        if fail_at_index is not None and index == fail_at_index:
            raise RetryableError(ErrorCode.FLOOD_WAIT, f"simulated failure at {op.op_code}")
        await usage.insert(
            account_id=account_id,
            op_type_id=op_type_id_by_code[op.op_code],
            task_id=task_id,
            task_type_id=task_type.id,
            units=op.units_per_execution,
        )
        payload["last_completed_step"] = op.op_code
        await _set_payload(task_id, payload)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_e8_retry_resumes_from_failed_op(e8_clean) -> None:
    account_id, _ = await _insert_account()
    repo = TaskQueueRepo()

    task_type = await TaskTypesRepo().get_by_code("collect_extra_data")
    assert task_type is not None
    pipeline = TASK_TYPE_OPS[COLLECT_EXTRA_DATA]
    op_type_id_by_code = {op.op_code: op.op_type_id for op in task_type.ops}

    task_id = await _insert_in_progress_task(task_type=task_type, account_id=account_id)

    # --- Попытка 1: падение на 3-м op (index=2). Записаны только op 0 и 1. ---
    await repo.begin_execution_attempt(task_id)
    with pytest.raises(RetryableError):
        await _run_collect_pipeline(
            task_id=task_id,
            account_id=account_id,
            task_type=task_type,
            op_type_id_by_code=op_type_id_by_code,
            fail_at_index=_FAIL_AT_INDEX,
        )

    rows_after_fail = await _usage_rows(task_id)
    assert len(rows_after_fail) == _FAIL_AT_INDEX
    payload_after_fail = await _get_payload(task_id)
    assert payload_after_fail["last_completed_step"] == pipeline[_FAIL_AT_INDEX - 1].op_code
    completed_op_ids = {row["op_type_id"] for row in rows_after_fail}

    # --- Попытка 2 (retry): продолжение с упавшего op до конца, без дублей. ---
    await repo.begin_execution_attempt(task_id)
    await _run_collect_pipeline(
        task_id=task_id,
        account_id=account_id,
        task_type=task_type,
        op_type_id_by_code=op_type_id_by_code,
        fail_at_index=None,
    )
    assert await repo.complete(task_id) is True

    rows_final = await _usage_rows(task_id)
    # Один INSERT на каждый op пайплайна — нет лишних/дублирующих списаний.
    assert len(rows_final) == len(pipeline)
    final_op_ids = [row["op_type_id"] for row in rows_final]
    assert len(final_op_ids) == len(set(final_op_ids))
    assert set(final_op_ids) == set(op_type_id_by_code.values())

    # Ранее выполненные op не списаны повторно (по одной строке на каждый).
    for op_id in completed_op_ids:
        assert final_op_ids.count(op_id) == 1

    payload_final = await _get_payload(task_id)
    assert payload_final["last_completed_step"] == pipeline[-1].op_code

    # Две реальные попытки execute учтены в attempt_count.
    async with db.acquire() as conn:
        attempt_count = await conn.fetchval(
            "SELECT attempt_count FROM task_queue WHERE id = $1", task_id
        )
    assert int(attempt_count) == 2
