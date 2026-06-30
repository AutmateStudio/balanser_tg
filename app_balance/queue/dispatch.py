"""C2/C3/C4/C5 — orchestration: reserve → resource check → adapter.execute → complete/postpone/retry."""

from __future__ import annotations



import logging

from decimal import Decimal, ROUND_UP
from enum import Enum



from app_balance.queue.accounts import Account, AccountsRepo, DualReserveResult
from datetime import datetime, timedelta, timezone

from app_balance.queue.error_codes import ErrorCode, classify_exception_code, normalize_error_code
from app_balance.queue.errors import (
    PermanentError,
    QueueTaskError,
    ResourceError,
    RetryableError,
)
from app_balance.queue.mock_adapter import TaskAdapter
from app_balance.queue.ops_catalog import MULTI_OP_TASK_TYPES
from app_balance.queue.per_op_reading import TaskType, TaskTypesRepo

from app_balance.queue.resource_check import ResourceCheckResult, ResourceChecker

from app_balance.queue.resource_usage import ResourceUsageRepo

from app_balance.queue.task_attempts import AttemptFinishStatus, TaskAttemptsRepo

from app_balance.queue.task_error_log import (
    bind_task_error_context,
    clear_task_error_context,
    log_queue_task_error,
)
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

        attempts: TaskAttemptsRepo | None = None,

        postpone_delay_seconds: int = 300,

        retry_delay_seconds: int = 60,

    ) -> None:

        self._queue = queue

        self._accounts = accounts

        self._task_types = task_types

        self._adapter = adapter

        self._usage = usage or ResourceUsageRepo()

        self._attempts = attempts or TaskAttemptsRepo()

        self._resource_check = resource_check or ResourceChecker(self._usage)

        self._postpone_delay_seconds = postpone_delay_seconds

        self._retry_delay_seconds = retry_delay_seconds



    async def dispatch(self, task: ClaimedTask) -> DispatchResult:

        reserved_ids: list[int] = []

        attempt_id: int | None = None
        attempt_number: int | None = None
        execute_account: Account | None = None
        task_type: TaskType | None = None

        try:
            task_type = await self._task_types.get_by_code(task.task_type_code)

            if task_type is None or not task_type.is_enabled:

                status = await self._queue.reschedule_or_fail(

                    task.id,

                    f"{ErrorCode.UNKNOWN_TASK_TYPE}:{task.task_type_code}",

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

            bind_task_error_context(
                task_id=task.id,
                account=execute_account.session_name,
            )

            attempt_number = await self._queue.begin_execution_attempt(task.id)

            if task_type.uses_two_accounts:
                accounts_by_role = {
                    "source": dual.source.id,
                    "target": dual.target.id,
                }
                source_account_id: int | None = dual.source.id
                target_account_id: int | None = dual.target.id
            else:
                accounts_by_role = {"primary": execute_account.id}
                source_account_id = None
                target_account_id = None

            attempt_id = await self._attempts.insert(
                task_id=task.id,
                task_type_id=task_type.id,
                account_id=execute_account.id,
                attempt_number=attempt_number,
                source_account_id=source_account_id,
                target_account_id=target_account_id,
            )

            is_multi_op = task_type.code in MULTI_OP_TASK_TYPES
            if is_multi_op:
                # Multi-op типы (collect/update) ведут учёт пошагово в adapter
                # (execute_multi_op_pipeline → record_op), чтобы не задвоить.
                await self._adapter.execute(
                    task,
                    account=execute_account,
                    task_type=task_type,
                    attempt_id=attempt_id,
                )
            else:
                # Single-call типы: ресурс списывается разом до RPC (D5 §7.3).
                await self._usage.record_for_task(
                    task_type=task_type,
                    task_id=task.id,
                    accounts_by_role=accounts_by_role,
                    task_attempt_id=attempt_id,
                )
                await self._adapter.execute(task, account=execute_account)

            await self._finish_attempt(attempt_id, status="success")

            if await self._queue.complete(task.id):
                if task.task_type_code == "parser_add_channel":
                    logger.info(
                        "dispatch: parser_add_channel completed task_id=%s session=%s",
                        task.id,
                        execute_account.session_name,
                    )
                return DispatchResult.COMPLETED

            return DispatchResult.RETRIED

        except ResourceError as exc:
            account_name = execute_account.session_name if execute_account else "-"
            log_queue_task_error(
                logger,
                exc,
                task_id=task.id,
                account=account_name,
            )
            await self._handle_execute_error(
                task.id,
                attempt_id,
                exc,
                queue_op="postpone",
                postpone_reason=exc.postpone_reason(),
            )
            return DispatchResult.POSTPONED

        except PermanentError as exc:
            account_name = execute_account.session_name if execute_account else "-"
            log_queue_task_error(
                logger,
                exc,
                task_id=task.id,
                account=account_name,
            )
            await self._sync_account_health_on_error(execute_account, exc)
            await self._handle_execute_error(
                task.id,
                attempt_id,
                exc,
                queue_op="fail",
            )
            return DispatchResult.FAILED

        except RetryableError as exc:
            account_name = execute_account.session_name if execute_account else "-"
            log_queue_task_error(
                logger,
                exc,
                task_id=task.id,
                account=account_name,
            )
            await self._sync_account_health_on_error(execute_account, exc)
            delay = (
                exc.retry_after_seconds
                if exc.retry_after_seconds is not None
                else self._calc_retry_delay(
                    task_type=task_type,
                    attempt_number=attempt_number,
                )
            )
            result = await self._handle_execute_error(
                task.id,
                attempt_id,
                exc,
                queue_op="retry",
                retry_delay_seconds=delay,
            )
            return result

        except Exception as exc:  # noqa: BLE001 — фиксируем любую ошибку попытки

            account_name = execute_account.session_name if execute_account else "-"
            log_queue_task_error(
                logger,
                exc,
                task_id=task.id,
                account=account_name,
            )

            result = await self._handle_execute_error(
                task.id,
                attempt_id,
                exc,
                queue_op="retry",
                retry_delay_seconds=self._calc_retry_delay(
                    task_type=task_type,
                    attempt_number=attempt_number,
                ),
            )
            return result

        finally:

            clear_task_error_context()

            for account_id in reserved_ids:

                await self._accounts.release(account_id)



    async def _handle_execute_error(
        self,
        task_id: int,
        attempt_id: int | None,
        exc: Exception,
        *,
        queue_op: str,
        retry_delay_seconds: int | None = None,
        last_error: str | None = None,
        postpone_reason: str | None = None,
    ) -> DispatchResult:
        """E1: единая финализация попытки и перевод задачи в retry/fail/postpone."""
        attempt_status, error_code = self._classify_attempt_error(exc)
        await self._finish_attempt(
            attempt_id,
            status=attempt_status,
            error_code=error_code,
            error_message=str(exc),
        )

        if queue_op == "postpone":
            await self._queue.postpone(
                task_id,
                self._postpone_delay_seconds,
                postpone_reason or error_code,
            )
            return DispatchResult.POSTPONED

        error_for_queue = (
            last_error
            if last_error is not None
            else classify_exception_code(exc)
        )

        if queue_op == "fail":
            status = await self._queue.fail(task_id, error_for_queue)
            if status is None:
                return DispatchResult.FAILED
            return DispatchResult.FAILED

        delay = retry_delay_seconds if retry_delay_seconds is not None else self._retry_delay_seconds
        status = await self._queue.reschedule_or_fail(
            task_id,
            error_for_queue,
            delay,
        )
        if status is None:
            return DispatchResult.RETRIED
        return (
            DispatchResult.FAILED
            if status == "failed"
            else DispatchResult.RETRIED
        )

    def _calc_retry_delay(
        self,
        *,
        task_type: TaskType | None,
        attempt_number: int | None,
    ) -> int:
        if task_type is None or attempt_number is None:
            return self._retry_delay_seconds
        base_delay = int(task_type.retry_delay_seconds)
        if base_delay <= 0:
            return self._retry_delay_seconds
        exponent = max(0, attempt_number - 1)
        multiplier = task_type.retry_backoff_multiplier
        delay = Decimal(base_delay) * (multiplier**exponent)
        if task_type.max_retry_delay_seconds > 0:
            delay = min(delay, Decimal(task_type.max_retry_delay_seconds))
        return int(delay.to_integral_value(rounding=ROUND_UP))

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

                ErrorCode.MISSING_DUAL_ACCOUNTS,

            )

            return None

        if source_id == target_id:

            await self._queue.postpone(

                task.id,

                self._postpone_delay_seconds,

                ErrorCode.DUAL_ACCOUNTS_SAME_ID,

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

                f"{ErrorCode.DUAL_ACCOUNT_RESERVE_FAILED}:{source_id}:{target_id}",

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

                f"{ErrorCode.ACCOUNT_RESERVE_FAILED}:{account_id}",

            )

            return None



        account = await self._accounts.get_by_id(account_id)

        if account is None:

            await self._accounts.release(account_id)

            await self._queue.postpone(

                task.id,

                self._postpone_delay_seconds,

                f"{ErrorCode.ACCOUNT_NOT_FOUND}:{account_id}",

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

                        else ErrorCode.INSUFFICIENT_RESOURCE

                    )

                else:

                    reason = ErrorCode.NO_AVAILABLE_ACCOUNT

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

        if check.reason_code and check.reason_code.startswith(f"{ErrorCode.NO_OPS_FOR_ROLE}:"):

            return check.reason_code

        if check.failing_op_code is not None and check.account_id is not None:

            return (

                f"{ErrorCode.INSUFFICIENT_RESOURCE}:{check.account_id}:{check.failing_op_code}"

            )

        return check.reason_code or ErrorCode.INSUFFICIENT_RESOURCE

    async def _finish_attempt(
        self,
        attempt_id: int | None,
        *,
        status: AttemptFinishStatus,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """B9 — финализация task_attempt; сбой учёта истории не ломает задачу."""
        if attempt_id is None:
            return
        try:
            await self._attempts.finish(
                attempt_id,
                status=status,
                error_code=error_code,
                error_message=error_message,
            )
        except Exception:  # noqa: BLE001 — история не должна влиять на статус задачи
            logger.exception(
                "dispatch: не удалось финализировать task_attempt id=%s", attempt_id
            )

    @staticmethod
    def _classify_attempt_error(exc: Exception) -> tuple[AttemptFinishStatus, str]:
        """Статус и краткий error_code попытки из исключения (B9, E1, E5)."""
        if isinstance(exc, TimeoutError):
            return "timeout", normalize_error_code(ErrorCode.TRANSIENT_ERROR)
        if isinstance(exc, QueueTaskError):
            return "error", normalize_error_code(exc.code)
        return "error", classify_exception_code(exc)

    async def _sync_account_health_on_error(
        self,
        account: Account | None,
        exc: Exception,
    ) -> None:
        """E2: flood → cooldown, ban → banned в PG accounts."""
        if account is None:
            return
        code = normalize_error_code(getattr(exc, "code", None))
        if isinstance(exc, RetryableError) and code == ErrorCode.FLOOD_WAIT:
            seconds = exc.retry_after_seconds
            if seconds is not None and seconds > 0:
                until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
                try:
                    await self._accounts.set_cooldown(account.session_name, until)
                except Exception:  # noqa: BLE001 — health sync не ломает dispatch
                    logger.warning(
                        "dispatch: не удалось set_cooldown для %s",
                        account.session_name,
                        exc_info=True,
                    )
        elif isinstance(exc, PermanentError) and code == ErrorCode.BANNED:
            try:
                await self._accounts.set_banned(
                    account.session_name,
                    reason=getattr(exc, "message", None),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "dispatch: не удалось set_banned для %s",
                    account.session_name,
                    exc_info=True,
                )
        elif isinstance(exc, PermanentError) and code == ErrorCode.ACCOUNT_UNAUTHORIZED:
            message = getattr(exc, "message", None) or str(exc)
            try:
                from discovery_api.session_registry import notify_session_unauthorized

                await notify_session_unauthorized(account.session_name, message)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "dispatch: notify_session_unauthorized недоступен для %s, fallback PG",
                    account.session_name,
                    exc_info=True,
                )
                try:
                    await self._accounts.set_account_error(
                        account.session_name,
                        reason=message,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "dispatch: не удалось set_account_error для %s",
                        account.session_name,
                        exc_info=True,
                    )

