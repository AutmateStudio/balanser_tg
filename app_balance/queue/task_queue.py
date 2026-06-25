"""B3 — enqueue + dedup для task_queue (ТЗ §9, §12; план B3).

Схема: DB/BD_schema.sql § task_queue + partial unique index
idx_task_queue_dedup_active (dedup_key WHERE status IN активные).
Настройки типа задачи (priority, max_attempts) берутся из task_types через B2.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

import asyncpg

from app_balance.queue.db import acquire, transaction
from app_balance.queue.error_codes import ErrorCode, error_code_prefix
from app_balance.queue.per_op_reading import TaskTypesRepo

if TYPE_CHECKING:
    from app_balance.queue.watchdog import WatchdogAutoRetryConfig

logger = logging.getLogger(__name__)

# C6 — стабильный код причины для мониторинга (§13.4, G1/G5).
WATCHDOG_STUCK_REASON = ErrorCode.WATCHDOG_TASK_TIMEOUT

# Статусы, в которых задача считается «активной» — совпадает с условием
# partial unique index idx_task_queue_dedup_active (BD_schema.sql).
ACTIVE_STATUSES = ("queued", "scheduled", "retry", "in_progress")


class UnknownTaskTypeError(ValueError):
    """task_type_code отсутствует в task_types или тип выключен."""


@dataclass(slots=True)
class EnqueueInput:
    task_type_code: str
    payload: dict[str, Any] | None = None
    dedup_key: str | None = None
    channel_id: int | None = None
    account_id: int | None = None
    source_account_id: int | None = None
    target_account_id: int | None = None
    priority: int | None = None
    run_after: datetime | None = None
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    created: bool
    task_id: int | None
    existing_task_id: int | None = None


@dataclass(frozen=True, slots=True)
class StuckTaskResult:
    """Задача, обработанная watchdog по таймауту (C6 stuck / G5 auto-retry).

    outcome — итоговый статус: 'stuck' (C6), 'retry' или 'failed' (G5).
    """

    id: int
    task_type_code: str
    locked_by: str | None
    account_id: int | None
    source_account_id: int | None
    target_account_id: int | None
    outcome: Literal["stuck", "retry", "failed"] = "stuck"
    attempt_count: int = 0
    max_attempts: int = 0
    watchdog_retry_count: int = 0


@dataclass(frozen=True, slots=True)
class ClaimedTask:
    """Захваченная воркером задача (статус уже in_progress, lock проставлен)."""

    id: int
    task_type_id: int
    task_type_code: str
    priority: int
    payload: dict[str, Any]
    channel_id: int | None
    account_id: int | None
    source_account_id: int | None
    target_account_id: int | None
    attempt_count: int
    max_attempts: int
    dedup_key: str | None
    locked_by: str | None
    locked_until: datetime | None


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    """Снимок задачи для read-only API (D10)."""

    id: int
    task_type_code: str
    status: str
    attempt_count: int
    postpone_count: int
    last_error: str | None
    last_error_code: str | None
    payload: dict[str, Any]
    run_after: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    last_error_at: datetime | None


_INSERT_SQL = """
INSERT INTO task_queue (
    task_type_id,
    task_type_code,
    status,
    priority,
    channel_id,
    account_id,
    source_account_id,
    target_account_id,
    payload,
    dedup_key,
    run_after,
    max_attempts,
    created_by
) VALUES (
    $1, $2, 'queued', $3,
    $4, $5, $6, $7,
    $8::jsonb, $9,
    COALESCE($10, now()),
    $11, $12
)
RETURNING id
"""

_FIND_ACTIVE_BY_DEDUP_SQL = f"""
SELECT id
FROM task_queue
WHERE dedup_key = $1
  AND status IN {ACTIVE_STATUSES}
ORDER BY id ASC
LIMIT 1
"""

_GET_BY_ID_SQL = """
SELECT
    id,
    task_type_code,
    status::text AS status,
    attempt_count,
    postpone_count,
    last_error,
    payload,
    run_after,
    started_at,
    finished_at,
    last_error_at
FROM task_queue
WHERE id = $1
"""

# Статусы, готовые к захвату (совпадает с idx_task_queue_claim_ready).
CLAIMABLE_STATUSES = ("queued", "scheduled", "retry")

# B4/C7: атомарный claim среди задач с максимальным priority; внутри
# этого уровня — случайный выбор (random()), без сортировки по created_at.
# CTE max_prio → ready с ORDER BY random() FOR UPDATE SKIP LOCKED.
_CLAIM_NEXT_SQL = f"""
WITH max_prio AS (
    SELECT COALESCE(MAX(priority), -2147483648) AS p
    FROM task_queue
    WHERE status IN {CLAIMABLE_STATUSES}
      AND run_after <= now()
      AND (locked_until IS NULL OR locked_until <= now())
      AND ($2::text[] IS NULL OR task_type_code = ANY($2::text[]))
),
ready AS (
    SELECT t.id
    FROM task_queue t
    CROSS JOIN max_prio m
    WHERE t.status IN {CLAIMABLE_STATUSES}
      AND t.run_after <= now()
      AND (t.locked_until IS NULL OR t.locked_until <= now())
      AND ($2::text[] IS NULL OR t.task_type_code = ANY($2::text[]))
      AND t.priority = m.p
    ORDER BY random()
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE task_queue AS t
SET status = 'in_progress',
    locked_by = $1,
    locked_at = now(),
    locked_until = now() + ($3 * interval '1 second'),
    started_at = COALESCE(t.started_at, now()),
    updated_at = now()
FROM ready
WHERE t.id = ready.id
RETURNING
    t.id, t.task_type_id, t.task_type_code, t.priority, t.payload,
    t.channel_id, t.account_id, t.source_account_id, t.target_account_id,
    t.attempt_count, t.max_attempts, t.dedup_key, t.locked_by, t.locked_until
"""


_CLAIM_BY_ID_SQL = """
UPDATE task_queue AS t
SET status = 'in_progress',
    locked_by = $2,
    locked_at = now(),
    locked_until = now() + ($3 * interval '1 second'),
    started_at = COALESCE(t.started_at, now()),
    updated_at = now()
WHERE t.id = $1
  AND t.status IN ('queued', 'scheduled', 'retry')
  AND t.run_after <= now()
  AND (t.locked_until IS NULL OR t.locked_until <= now())
RETURNING
    t.id, t.task_type_id, t.task_type_code, t.priority, t.payload,
    t.channel_id, t.account_id, t.source_account_id, t.target_account_id,
    t.attempt_count, t.max_attempts, t.dedup_key, t.locked_by, t.locked_until
"""


_COMPLETE_SQL = """
UPDATE task_queue
SET status = 'done',
    finished_at = now(),
    locked_by = NULL,
    locked_at = NULL,
    locked_until = NULL,
    updated_at = now()
WHERE id = $1
  AND status = 'in_progress'
"""

# Если попытки исчерпаны (attempt_count >= max_attempts) → failed, иначе retry
# с отложенным run_after. Lock снимается в любом случае.
_RESCHEDULE_OR_FAIL_SQL = """
UPDATE task_queue
SET status = CASE WHEN attempt_count >= max_attempts THEN 'failed'::task_status
                  ELSE 'retry'::task_status END,
    last_error = $2,
    last_error_at = now(),
    finished_at = CASE WHEN attempt_count >= max_attempts THEN now()
                       ELSE finished_at END,
    run_after = CASE WHEN attempt_count >= max_attempts THEN run_after
                     ELSE now() + ($3 * interval '1 second') END,
    locked_by = NULL,
    locked_at = NULL,
    locked_until = NULL,
    updated_at = now()
WHERE id = $1
  AND status = 'in_progress'
RETURNING status::text
"""

# E1: немедленный failed без retry (PermanentError).
_FAIL_SQL = """
UPDATE task_queue
SET status = 'failed'::task_status,
    last_error = $2,
    last_error_at = now(),
    finished_at = now(),
    locked_by = NULL,
    locked_at = NULL,
    locked_until = NULL,
    updated_at = now()
WHERE id = $1
  AND status = 'in_progress'
RETURNING status::text
"""

_ASSIGN_ACCOUNT_SQL = """
UPDATE task_queue
SET account_id = $2,
    updated_at = now()
WHERE id = $1
"""

# E6: фиксирует op-код последнего успешного шага multi-op пайплайна в payload
# (jsonb_set по одному ключу, остальные ключи payload не трогаем).
_SET_LAST_COMPLETED_STEP_SQL = """
UPDATE task_queue
SET payload = jsonb_set(
        COALESCE(payload, '{}'::jsonb),
        '{last_completed_step}',
        to_jsonb($2::text),
        true
    ),
    updated_at = now()
WHERE id = $1
"""

# ТЗ §9.3: attempt_count — только при передаче задачи аккаунту (до execute).
_BEGIN_EXECUTION_ATTEMPT_SQL = """
UPDATE task_queue
SET attempt_count = attempt_count + 1,
    updated_at = now()
WHERE id = $1
RETURNING attempt_count
"""

# B5: отложить задачу без расхода ресурса и без increment attempt_count.
_POSTPONE_SQL = """
UPDATE task_queue
SET status = 'scheduled',
    run_after = now() + ($2 * interval '1 second'),
    postpone_count = postpone_count + 1,
    last_error = COALESCE($3, last_error),
    last_error_at = CASE WHEN $3 IS NOT NULL THEN now() ELSE last_error_at END,
    locked_by = NULL,
    locked_at = NULL,
    locked_until = NULL,
    updated_at = now()
WHERE id = $1
  AND status = 'in_progress'
"""

# C6/G5: in_progress дольше task_timeout_seconds → stuck (C6) либо auto-retry (G5).
# Параметры: $1 limit, $2 auto_retry_enabled, $3 watchdog max_attempts (cap),
# $4 retry delay (s), $5 last_error код. Во всех ветках — release аккаунтов + снятие lock.
# can_retry = enabled И watchdog_retry_count < cap И attempt_count < max_attempts.
_MARK_STUCK_TIMED_OUT_SQL = """
WITH timed_out AS (
    SELECT t.id
    FROM task_queue t
    JOIN task_types tt ON tt.id = t.task_type_id
    WHERE t.status = 'in_progress'
      AND t.locked_at IS NOT NULL
      AND t.locked_at + (tt.task_timeout_seconds * interval '1 second') < now()
    ORDER BY t.locked_at ASC
    LIMIT $1
    FOR UPDATE SKIP LOCKED
),
computed AS (
    SELECT t.id,
           COALESCE((t.payload->>'watchdog_retry_count')::int, 0) AS wd_count,
           (
               $2::boolean
               AND COALESCE((t.payload->>'watchdog_retry_count')::int, 0) < $3::int
               AND t.attempt_count < t.max_attempts
           ) AS can_retry
    FROM task_queue t
    JOIN timed_out x ON x.id = t.id
),
marked AS (
    UPDATE task_queue t
    SET status = CASE
            WHEN NOT $2::boolean THEN 'stuck'::task_status
            WHEN c.can_retry THEN 'retry'::task_status
            ELSE 'failed'::task_status
        END,
        last_error = $5,
        last_error_at = now(),
        finished_at = CASE
            WHEN $2::boolean AND NOT c.can_retry THEN now()
            ELSE t.finished_at
        END,
        run_after = CASE
            WHEN $2::boolean AND c.can_retry
                THEN now() + ($4::int * interval '1 second')
            ELSE t.run_after
        END,
        payload = CASE
            WHEN $2::boolean AND c.can_retry THEN jsonb_set(
                COALESCE(t.payload, '{}'::jsonb),
                '{watchdog_retry_count}',
                to_jsonb(c.wd_count + 1),
                true
            )
            ELSE t.payload
        END,
        locked_by = NULL,
        locked_at = NULL,
        locked_until = NULL,
        updated_at = now()
    FROM computed c
    WHERE t.id = c.id
    RETURNING t.id, t.task_type_code, t.locked_by,
              t.account_id, t.source_account_id, t.target_account_id,
              t.status::text AS outcome, t.attempt_count, t.max_attempts,
              COALESCE((t.payload->>'watchdog_retry_count')::int, 0)
                  AS watchdog_retry_count
),
released AS (
    UPDATE accounts a
    SET current_task_id = NULL
    WHERE a.current_task_id IN (SELECT id FROM marked)
    RETURNING a.id
)
SELECT id, task_type_code, locked_by,
       account_id, source_account_id, target_account_id,
       outcome, attempt_count, max_attempts, watchdog_retry_count
FROM marked
"""


def _row_to_stuck(row) -> StuckTaskResult:
    return StuckTaskResult(
        id=row["id"],
        task_type_code=row["task_type_code"],
        locked_by=row["locked_by"],
        account_id=row["account_id"],
        source_account_id=row["source_account_id"],
        target_account_id=row["target_account_id"],
        outcome=row["outcome"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        watchdog_retry_count=row["watchdog_retry_count"],
    )


def _row_to_claimed(row) -> ClaimedTask:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ClaimedTask(
        id=row["id"],
        task_type_id=row["task_type_id"],
        task_type_code=row["task_type_code"],
        priority=row["priority"],
        payload=payload or {},
        channel_id=row["channel_id"],
        account_id=row["account_id"],
        source_account_id=row["source_account_id"],
        target_account_id=row["target_account_id"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        dedup_key=row["dedup_key"],
        locked_by=row["locked_by"],
        locked_until=row["locked_until"],
    )


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        return json.loads(raw) or {}
    return dict(raw or {})


def _row_to_snapshot(row) -> TaskSnapshot:
    last_error = row["last_error"]
    return TaskSnapshot(
        id=row["id"],
        task_type_code=row["task_type_code"],
        status=row["status"],
        attempt_count=row["attempt_count"],
        postpone_count=row["postpone_count"],
        last_error=last_error,
        last_error_code=error_code_prefix(last_error),
        payload=_parse_payload(row["payload"]),
        run_after=row["run_after"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        last_error_at=row["last_error_at"],
    )


class TaskQueueRepo:
    """Создание и захват задач в очереди (B3 enqueue + B4 claim)."""

    def __init__(self, task_types: TaskTypesRepo | None = None) -> None:
        self._task_types = task_types or TaskTypesRepo()

    async def enqueue(self, data: EnqueueInput) -> EnqueueResult:
        task_type = await self._task_types.get_by_code(data.task_type_code)
        if task_type is None or not task_type.is_enabled:
            raise UnknownTaskTypeError(
                f"Тип задачи '{data.task_type_code}' не найден или выключен"
            )

        priority = data.priority if data.priority is not None else task_type.default_priority
        payload_json = json.dumps(data.payload or {})

        async with acquire() as conn:
            try:
                task_id = await conn.fetchval(
                    _INSERT_SQL,
                    task_type.id,
                    task_type.code,
                    priority,
                    data.channel_id,
                    data.account_id,
                    data.source_account_id,
                    data.target_account_id,
                    payload_json,
                    data.dedup_key,
                    data.run_after,
                    task_type.max_attempts,
                    data.created_by,
                )
            except asyncpg.UniqueViolationError:
                existing_id = await conn.fetchval(
                    _FIND_ACTIVE_BY_DEDUP_SQL, data.dedup_key
                )
                return EnqueueResult(
                    created=False,
                    task_id=None,
                    existing_task_id=existing_id,
                )

        return EnqueueResult(created=True, task_id=task_id)

    async def get_by_id(self, task_id: int) -> TaskSnapshot | None:
        """D10: read-only снимок задачи по id."""
        async with acquire() as conn:
            row = await conn.fetchrow(_GET_BY_ID_SQL, task_id)
        if row is None:
            return None
        return _row_to_snapshot(row)

    async def claim_next(
        self,
        locked_by: str,
        lock_ttl_seconds: int = 300,
        task_type_codes: list[str] | None = None,
    ) -> ClaimedTask | None:
        """B4/C7: атомарно захватывает одну готовую задачу (FOR UPDATE SKIP LOCKED).

        Алгоритм: среди готовых задач находит максимальный priority, затем
        случайно выбирает одну из этого уровня (ORDER BY random()).
        Готовая = status IN (queued, scheduled, retry), run_after наступил,
        прежний lock истёк. Переводит в in_progress (attempt_count не меняется).
        None — очередь пуста / всё занято другими воркерами.
        """
        async with acquire() as conn:
            row = await conn.fetchrow(
                _CLAIM_NEXT_SQL,
                locked_by,
                task_type_codes,
                lock_ttl_seconds,
            )
            if row is None:
                return None
            return _row_to_claimed(row)

    async def claim_by_id(
        self,
        task_id: int,
        locked_by: str,
        lock_ttl_seconds: int = 300,
    ) -> ClaimedTask | None:
        """Захват конкретной задачи по id (integration-тесты на shared PG)."""
        async with acquire() as conn:
            row = await conn.fetchrow(
                _CLAIM_BY_ID_SQL,
                task_id,
                locked_by,
                lock_ttl_seconds,
            )
            if row is None:
                return None
            return _row_to_claimed(row)

    async def complete(self, task_id: int) -> bool:
        """Успешное завершение: in_progress → done, снимает lock.

        False — задача уже не in_progress (например, watchdog → stuck).
        """
        async with acquire() as conn:
            result = await conn.execute(_COMPLETE_SQL, task_id)
            updated = int(result.split()[-1])
            if updated == 0:
                logger.debug(
                    "complete: задача id=%s уже не in_progress (watchdog?)",
                    task_id,
                )
                return False
            return True

    async def reschedule_or_fail(
        self,
        task_id: int,
        error: str | None = None,
        retry_delay_seconds: int = 60,
    ) -> str | None:
        """Ошибка попытки: retry (если остались попытки) либо failed.

        Возвращает итоговый статус ('retry' | 'failed') или None, если задача
        уже не in_progress.
        """
        async with acquire() as conn:
            status = await conn.fetchval(
                _RESCHEDULE_OR_FAIL_SQL, task_id, error, retry_delay_seconds
            )
            if status is None:
                logger.debug(
                    "reschedule_or_fail: задача id=%s уже не in_progress (watchdog?)",
                    task_id,
                )
            return status

    async def fail(self, task_id: int, error: str | None = None) -> str | None:
        """E1: немедленный failed (PermanentError), без учёта оставшихся попыток.

        Возвращает 'failed' или None, если задача уже не in_progress.
        """
        async with acquire() as conn:
            status = await conn.fetchval(_FAIL_SQL, task_id, error)
            if status is None:
                logger.debug(
                    "fail: задача id=%s уже не in_progress (watchdog?)",
                    task_id,
                )
            return status

    async def assign_account(self, task_id: int, account_id: int) -> None:
        """Записывает подобранный аккаунт в task_queue (после pick_and_reserve)."""
        async with acquire() as conn:
            await conn.execute(_ASSIGN_ACCOUNT_SQL, task_id, account_id)

    async def set_last_completed_step(self, task_id: int, step: str) -> None:
        """E6: сохраняет op-код последнего успешного шага в payload (идемпотентность).

        При retry многошаговой задачи адаптер по этому значению пропускает уже
        выполненные op и не списывает за них ресурс повторно (ТЗ §29).
        """
        async with acquire() as conn:
            await conn.execute(_SET_LAST_COMPLETED_STEP_SQL, task_id, step)

    async def begin_execution_attempt(self, task_id: int) -> int:
        """Инкремент attempt_count при передаче задачи аккаунту (ТЗ §9.3)."""
        async with acquire() as conn:
            val = await conn.fetchval(_BEGIN_EXECUTION_ATTEMPT_SQL, task_id)
            if val is None:
                raise ValueError(f"task_queue id={task_id} not found")
            return int(val)

    async def postpone(
        self,
        task_id: int,
        delay_seconds: int = 300,
        reason: str | None = None,
    ) -> bool:
        """Отложить задачу: in_progress → scheduled без расхода ресурса.

        Инкрементирует postpone_count, снимает lock, не трогает attempt_count.
        False — задача уже не in_progress.
        """
        async with acquire() as conn:
            result = await conn.execute(
                _POSTPONE_SQL, task_id, delay_seconds, reason
            )
            updated = int(result.split()[-1])
            if updated == 0:
                logger.debug(
                    "postpone: задача id=%s уже не in_progress (watchdog?)",
                    task_id,
                )
                return False
            return True

    async def mark_stuck_timed_out(
        self,
        *,
        limit: int = 100,
        auto_retry: "WatchdogAutoRetryConfig | None" = None,
    ) -> list[StuckTaskResult]:
        """C6/G5: обрабатывает зависшие in_progress и освобождает аккаунты.

        Без auto_retry (или при enabled=False) — поведение C6: → stuck.
        При auto_retry.enabled — G5: → retry (если остались попытки) либо failed.
        """
        enabled = bool(auto_retry and auto_retry.enabled)
        cap = auto_retry.max_attempts if auto_retry else 0
        delay = auto_retry.delay_seconds if auto_retry else 0
        async with transaction() as conn:
            rows = await conn.fetch(
                _MARK_STUCK_TIMED_OUT_SQL,
                limit,
                enabled,
                cap,
                delay,
                WATCHDOG_STUCK_REASON.value,
            )
            return [_row_to_stuck(row) for row in rows]
