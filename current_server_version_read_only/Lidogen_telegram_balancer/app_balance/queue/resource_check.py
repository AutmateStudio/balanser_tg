"""C5 — проверка min_available_resource_percent per-op (ТЗ §8, §16; план C5).



Для каждого enabled op из task_type_ops (с учётом account_role) проверяет

available_resource_percent через ResourceUsageRepo.op_availability.

"""

from __future__ import annotations



from dataclasses import dataclass



from app_balance.queue.per_op_reading import TaskOpAccountRole, TaskType

from app_balance.queue.resource_usage import ResourceUsageRepo





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

        threshold = task_type.min_available_resource_percent

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

                reason_code=f"no_ops_for_role:{account_role}",

            )



        for op in ops:

            availability = await self._usage.op_availability(account_id, op.op_type_id)

            if availability is None:

                return ResourceCheckResult(

                    ok=False,

                    threshold=threshold,

                    failing_op_code=op.op_code,

                    account_id=account_id,

                    reason_code="missing_availability",

                )

            if availability.available_resource_percent < threshold:

                return ResourceCheckResult(

                    ok=False,

                    threshold=threshold,

                    failing_op_code=op.op_code,

                    available_percent=availability.available_resource_percent,

                    account_id=account_id,

                    reason_code="insufficient_resource",

                )



        return ResourceCheckResult(

            ok=True,

            threshold=threshold,

            account_id=account_id,

        )


