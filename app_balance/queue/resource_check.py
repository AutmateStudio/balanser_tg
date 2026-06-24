"""C5 — проверка min_available_resource_percent per-op (ТЗ §8, §16; план C5).



Для каждого enabled op из task_type_ops (с учётом account_role) проверяет

available_resource_percent через ResourceUsageRepo.op_availability.

"""

from __future__ import annotations



import logging

import os

from dataclasses import dataclass



from app_balance.queue.error_codes import ErrorCode
from app_balance.queue.per_op_reading import TaskOpAccountRole, TaskType

from app_balance.queue.resource_usage import ResourceUsageRepo



log = logging.getLogger(__name__)



_THRESHOLD_OVERRIDE_ENV = "RESOURCE_MIN_AVAILABLE_PERCENT"



def resolve_threshold(db_threshold: int) -> int:
    """Порог min_available_resource_percent с env-override.

    Если задан RESOURCE_MIN_AVAILABLE_PERCENT (целое 0..100) — он заменяет
    значение из task_types. Иначе используется значение из БД (seed/конфиг).
    Невалидное значение игнорируется (с предупреждением) → fallback на БД.
    """
    raw = os.getenv(_THRESHOLD_OVERRIDE_ENV, "").strip()
    if not raw:
        return db_threshold
    try:
        value = int(raw)
    except ValueError:
        log.warning(
            "%s=%r не целое число — игнорирую, использую порог из БД (%s)",
            _THRESHOLD_OVERRIDE_ENV,
            raw,
            db_threshold,
        )
        return db_threshold
    if not 0 <= value <= 100:
        log.warning(
            "%s=%s вне диапазона 0..100 — игнорирую, использую порог из БД (%s)",
            _THRESHOLD_OVERRIDE_ENV,
            value,
            db_threshold,
        )
        return db_threshold
    return value





@dataclass(frozen=True, slots=True)

class ResourceCheckResult:

    ok: bool

    threshold: int

    failing_op_code: str | None = None

    available_percent: float | None = None

    account_id: int | None = None

    reason_code: str | None = None





class ResourceChecker:

    """Per-op проверка ресурса аккаунта перед execute (C5)."""



    def __init__(self, usage: ResourceUsageRepo) -> None:

        self._usage = usage



    async def check_account(

        self,

        account_id: int,

        task_type: TaskType,

        *,

        account_role: TaskOpAccountRole = "primary",

    ) -> ResourceCheckResult:

        threshold = resolve_threshold(task_type.min_available_resource_percent)

        ops = [

            op

            for op in task_type.ops

            if op.account_role == account_role and op.op_is_enabled

        ]

        if not ops:

            return ResourceCheckResult(

                ok=False,

                threshold=threshold,

                account_id=account_id,

                reason_code=f"{ErrorCode.NO_OPS_FOR_ROLE}:{account_role}",

            )



        for op in ops:

            availability = await self._usage.op_availability(account_id, op.op_type_id)

            if availability is None:

                return ResourceCheckResult(

                    ok=False,

                    threshold=threshold,

                    failing_op_code=op.op_code,

                    account_id=account_id,

                    reason_code=ErrorCode.MISSING_AVAILABILITY,

                )

            if availability.available_resource_percent < threshold:

                return ResourceCheckResult(

                    ok=False,

                    threshold=threshold,

                    failing_op_code=op.op_code,

                    available_percent=availability.available_resource_percent,

                    account_id=account_id,

                    reason_code=ErrorCode.INSUFFICIENT_RESOURCE,

                )



        return ResourceCheckResult(

            ok=True,

            threshold=threshold,

            account_id=account_id,

        )


