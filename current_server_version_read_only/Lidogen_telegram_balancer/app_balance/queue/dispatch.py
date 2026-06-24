"""C2/C3/C4/C5 — orchestration: reserve → resource check → adapter.execute → complete/postpone/retry."""

from __future__ import annotations



import logging

from enum import Enum



from app_balance.queue.accounts import Account, AccountsRepo, DualReserveResult

from app_balance.queue.mock_adapter import TaskAdapter

from app_balance.queue.per_op_reading import TaskType, TaskTypesRepo

from app_balance.queue.resource_check import ResourceCheckResult, ResourceChecker

from app_balance.queue.resource_usage import ResourceUsageRepo

from app_balance.queue.task_queue import ClaimedTask, TaskQueueRepo



logger = logging.getLogger(__name__)





class DispatchResult(str, Enum):

    COMPLETED = "completed"

    POSTPONED = "postponed"

    RETRIED = "retried"

    FAILED = "failed"





class TaskDispatcher:

    """Полный цикл исполнения одной захваченной задачи."""



    def __init__(

        self,

        queue: TaskQueueRepo,

        accounts: AccountsRepo,

        task_types: TaskTypesRepo,

        adapter: TaskAdapter,

        *,

        resource_check: ResourceChecker | None = None,

        usage: ResourceUsageRepo | None = None,

        postpone_delay_seconds: int = 300,

        retry_delay_seconds: int = 60,

    ) -> None:

        self._queue = queue

        self._accounts = accounts

        self._task_types = task_types

        self._adapter = adapter

        self._usage = usage or ResourceUsageRepo()

        self._resource_check = resource_check or ResourceChecker(self._usage)

        self._postpone_delay_seconds = postpone_delay_seconds

        self._retry_delay_seconds = retry_delay_seconds



    async def dispatch(self, task: ClaimedTask) -> DispatchResult:

        reserved_ids: list[int] = []

        try:

            task_type = await self._task_types.get_by_code(task.task_type_code)

            if task_type is None or not task_type.is_enabled:

                status = await self._queue.reschedule_or_fail(

                    task.id,

                    f"unknown_task_type:{task.task_type_code}",

                    self._retry_delay_seconds,

                )

                if status is None:

                    return DispatchResult.RETRIED

                return (

                    DispatchResult.FAILED

                    if status == "failed"

                    else DispatchResult.RETRIED

                )



            if task_type.uses_two_accounts:

                dual = await self._reserve_dual_accounts(task, task_type)

                if dual is None:

                    return DispatchResult.POSTPONED

                reserved_ids.extend([dual.source.id, dual.target.id])

                execute_account = dual.target

            else:

                account = await self._reserve_account(task, task_type)

                if account is None:

                    return DispatchResult.POSTPONED

                reserved_ids.append(account.id)

                if task.account_id is None:

                    await self._queue.assign_account(task.id, account.id)

                execute_account = account



            await self._queue.begin_execution_attempt(task.id)

            if task_type.uses_two_accounts:
                accounts_by_role = {
                    "source": dual.source.id,
                    "target": dual.target.id,
                }
            else:
                accounts_by_role = {"primary": execute_account.id}

            await self._usage.record_for_task(
                task_type=task_type,
                task_id=task.id,
                accounts_by_role=accounts_by_role,
            )

            await self._adapter.execute(task, account=execute_account)

            if await self._queue.complete(task.id):

                return DispatchResult.COMPLETED

            return DispatchResult.RETRIED

        except Exception as exc:  # noqa: BLE001 — фиксируем любую ошибку попытки

            logger.exception("dispatch: ошибка задачи id=%s", task.id)

            status = await self._queue.reschedule_or_fail(

                task.id, str(exc), self._retry_delay_seconds

            )

            if status is None:

                return DispatchResult.RETRIED

            return (

                DispatchResult.FAILED

                if status == "failed"

                else DispatchResult.RETRIED

            )

        finally:

            for account_id in reserved_ids:

                await self._accounts.release(account_id)



    async def _reserve_account(

        self, task: ClaimedTask, task_type: TaskType

    ) -> Account | None:

        if task.account_id is not None:

            return await self._reserve_fixed_account(task, task_type)



        return await self._reserve_auto_pick_account(task, task_type)



    async def _reserve_dual_accounts(

        self, task: ClaimedTask, task_type: TaskType

    ) -> DualReserveResult | None:

        source_id = task.source_account_id

        target_id = task.target_account_id

        if source_id is None or target_id is None:

            await self._queue.postpone(

                task.id,

                self._postpone_delay_seconds,

                "missing_dual_accounts",

            )

            return None

        if source_id == target_id:

            await self._queue.postpone(

                task.id,

                self._postpone_delay_seconds,

                "dual_accounts_same_id",

            )

            return None



        source_check = await self._resource_check.check_account(

            source_id, task_type, account_role="source"

        )

        if not source_check.ok:

            await self._queue.postpone(

                task.id,

                self._postpone_delay_seconds,

                self._resource_postpone_reason(source_check),

            )

            return None



        target_check = await self._resource_check.check_account(

            target_id, task_type, account_role="target"

        )

        if not target_check.ok:

            await self._queue.postpone(

                task.id,

                self._postpone_delay_seconds,

                self._resource_postpone_reason(target_check),

            )

            return None



        dual = await self._accounts.reserve_pair(source_id, target_id, task.id)

        if dual is None:

            await self._queue.postpone(

                task.id,

                self._postpone_delay_seconds,

                f"dual_account_reserve_failed:{source_id}:{target_id}",

            )

            return None

        return dual



    async def _reserve_fixed_account(

        self, task: ClaimedTask, task_type: TaskType

    ) -> Account | None:

        account_id = task.account_id

        assert account_id is not None



        ok = await self._accounts.reserve(account_id, task.id)

        if not ok:

            await self._queue.postpone(

                task.id,

                self._postpone_delay_seconds,

                f"account_reserve_failed:{account_id}",

            )

            return None



        account = await self._accounts.get_by_id(account_id)

        if account is None:

            await self._accounts.release(account_id)

            await self._queue.postpone(

                task.id,

                self._postpone_delay_seconds,

                f"account_not_found:{account_id}",

            )

            return None



        check = await self._resource_check.check_account(account.id, task_type)

        if check.ok:

            return account



        await self._accounts.release(account.id)

        await self._queue.postpone(

            task.id,

            self._postpone_delay_seconds,

            self._resource_postpone_reason(check),

        )

        return None



    async def _reserve_auto_pick_account(

        self, task: ClaimedTask, task_type: TaskType

    ) -> Account | None:

        rejected_ids: set[int] = set()

        last_check: ResourceCheckResult | None = None

        while True:

            exclude = frozenset(rejected_ids) if rejected_ids else None

            account = await self._accounts.pick_and_reserve(

                task.id, exclude_account_ids=exclude

            )

            if account is None:

                if rejected_ids:

                    reason = (

                        self._resource_postpone_reason(last_check)

                        if last_check is not None

                        else "insufficient_resource"

                    )

                else:

                    reason = "no_available_account"

                await self._queue.postpone(

                    task.id,

                    self._postpone_delay_seconds,

                    reason,

                )

                return None



            check = await self._resource_check.check_account(account.id, task_type)

            if check.ok:

                return account



            await self._accounts.release(account.id)

            rejected_ids.add(account.id)

            last_check = check



    @staticmethod

    def _resource_postpone_reason(check: ResourceCheckResult) -> str:

        if check.reason_code and check.reason_code.startswith("no_ops_for_role:"):

            return check.reason_code

        if check.failing_op_code is not None and check.account_id is not None:

            return (

                f"insufficient_resource:{check.account_id}:{check.failing_op_code}"

            )

        return check.reason_code or "insufficient_resource"

