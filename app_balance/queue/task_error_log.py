"""Структурированное логирование ошибок задач очереди и resolve."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from app_balance.queue.error_codes import ErrorCode

_task_id: ContextVar[int | None] = ContextVar("task_error_log.task_id", default=None)
_account: ContextVar[str | None] = ContextVar("task_error_log.account", default=None)
_bind_tokens: ContextVar[list[tuple[object, object]] | None] = ContextVar(
    "task_error_log.bind_tokens", default=None
)


def current_task_id() -> int | None:
    return _task_id.get()


def current_account() -> str | None:
    return _account.get()


def bind_task_error_context(*, task_id: int | None, account: str | None) -> None:
    """Привязать task_id/account к текущему async-контексту до clear_task_error_context()."""
    tokens = _bind_tokens.get()
    if tokens is None:
        tokens = []
        _bind_tokens.set(tokens)
    tokens.append((_task_id.set(task_id), _account.set(account)))


def clear_task_error_context() -> None:
    tokens = _bind_tokens.get()
    if not tokens:
        return
    while tokens:
        token_id, token_acc = tokens.pop()
        _task_id.reset(token_id)
        _account.reset(token_acc)


@contextmanager
def task_error_context(*, task_id: int | None, account: str | None) -> Iterator[None]:
    token_id = _task_id.set(task_id)
    token_acc = _account.set(account)
    try:
        yield
    finally:
        _task_id.reset(token_id)
        _account.reset(token_acc)


def error_type_label(exc: BaseException) -> str:
    from app_balance.queue.errors import PermanentError, ResourceError, RetryableError

    if isinstance(exc, PermanentError):
        return "FATAL"
    if isinstance(exc, ResourceError):
        return "RESOURCE"
    if isinstance(exc, RetryableError):
        return "RETRYABLE"
    return "UNEXPECTED"


def _parse_flood_seconds(message: str) -> int | None:
    try:
        from discovery_api.session_health import parse_flood_wait_seconds

        return parse_flood_wait_seconds(message)
    except ImportError:
        return None


def flood_wait_info(message: str, exc: BaseException | None = None) -> tuple[bool, int]:
    try:
        from telethon.errors import FloodWaitError
    except ImportError:
        FloodWaitError = ()  # type: ignore[misc, assignment]

    if exc is not None and isinstance(exc, FloodWaitError):
        return True, int(getattr(exc, "seconds", 0) or 0)

    from app_balance.queue.errors import RetryableError

    if isinstance(exc, RetryableError) and exc.code == ErrorCode.FLOOD_WAIT:
        secs = exc.retry_after_seconds
        if secs is None:
            secs = _parse_flood_seconds(message)
        return True, int(secs or 0)

    parsed = _parse_flood_seconds(message)
    if parsed is not None:
        return True, parsed
    return False, 0


def format_task_error_line(
    message: str,
    *,
    account: str | None = None,
    task_id: int | None = None,
    error_type: str,
    is_flood: bool = False,
    flood_wait_seconds: int = 0,
) -> str:
    acc = (account or current_account() or "-").strip() or "-"
    tid = task_id if task_id is not None else current_task_id()
    tid_s = str(tid) if tid is not None else "-"
    return (
        f"ERROR: ACCOUNT - {acc} TASK_ID - {tid_s} "
        f"TYPE - {error_type} IS_FLOOD - {str(is_flood).upper()} "
        f"FLOOD_WAIT-{int(flood_wait_seconds)} | {message}"
    )


def log_task_error(
    logger: logging.Logger,
    message: str,
    *,
    exc: BaseException | None = None,
    account: str | None = None,
    task_id: int | None = None,
    error_type: str | None = None,
    level: int = logging.ERROR,
    skip_if_queued: bool = False,
) -> None:
    """skip_if_queued: не дублировать строку, если ошибка уже залогируется в dispatch."""
    if skip_if_queued and current_task_id() is not None:
        return

    et = error_type or (error_type_label(exc) if exc else "UNEXPECTED")
    is_flood, fw = flood_wait_info(message, exc)
    line = format_task_error_line(
        message,
        account=account,
        task_id=task_id,
        error_type=et,
        is_flood=is_flood,
        flood_wait_seconds=fw,
    )
    logger.log(level, line)


def log_queue_task_error(
    logger: logging.Logger,
    exc: BaseException,
    *,
    task_id: int,
    account: str,
    level: int = logging.ERROR,
) -> None:
    message = getattr(exc, "message", None) or str(exc)
    log_task_error(
        logger,
        str(message),
        exc=exc,
        account=account,
        task_id=task_id,
        level=level,
    )


def display_account_name(session_name: str | None) -> str:
    """Короткое имя аккаунта для строк OK/POSTPONE (basename без пути)."""
    raw = (session_name or "").strip()
    if not raw:
        return "-"
    try:
        from app_balance.queue.accounts_sync import normalize_session_name

        return normalize_session_name(raw)
    except ImportError:
        base = raw.replace("\\", "/").rsplit("/", 1)[-1]
        if base.endswith(".session"):
            base = base[: -len(".session")]
        return base or "-"


def format_task_ok_line(
    task_id: int,
    task_type_name: str,
    account: str | None = None,
) -> str:
    type_name = (task_type_name or "-").strip() or "-"
    acc = display_account_name(account)
    return f"OK {task_id} {type_name} {acc}"


def format_task_postpone_line(
    task_id: int,
    task_type_name: str,
    account: str | None,
    delay_seconds: int,
) -> str:
    type_name = (task_type_name or "-").strip() or "-"
    acc = display_account_name(account)
    return f"POSTPONE {task_id} {type_name} {acc} {int(delay_seconds)}"


def log_task_ok(
    logger: logging.Logger,
    task_id: int,
    task_type_name: str,
    account: str | None = None,
) -> None:
    logger.info(format_task_ok_line(task_id, task_type_name, account))


def log_task_postpone(
    logger: logging.Logger,
    task_id: int,
    task_type_name: str,
    account: str | None,
    delay_seconds: int,
) -> None:
    logger.info(format_task_postpone_line(task_id, task_type_name, account, delay_seconds))
