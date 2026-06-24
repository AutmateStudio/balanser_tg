"""E1 — typed errors адаптера очереди (ТЗ §27).

Контракт: dispatch принимает решение по типу исключения, без парсинга строк.
E2 расширит маппинг Telethon через classify_telethon_error.
"""
from __future__ import annotations

from app_balance.queue.error_codes import ErrorCode

FLOOD_WAIT = ErrorCode.FLOOD_WAIT
ACCOUNT_BANNED = ErrorCode.BANNED
TRANSIENT = ErrorCode.TRANSIENT_ERROR
FATAL = ErrorCode.UNEXPECTED_ERROR

# Алиасы для E2-тестов и dispatch (значения = ErrorCode).
FLOOD_WAIT = ErrorCode.FLOOD_WAIT
ACCOUNT_BANNED = ErrorCode.BANNED
TRANSIENT = ErrorCode.TRANSIENT_ERROR
FATAL = "fatal"


class QueueTaskError(Exception):
    """Базовая ошибка выполнения задачи с машиночитаемым кодом (E5)."""

    code: str
    message: str

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)


class RetryableError(QueueTaskError):
    """Повторить позже (retry / run_after)."""

    retry_after_seconds: int | None

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code, message)
        self.retry_after_seconds = retry_after_seconds


class PermanentError(QueueTaskError):
    """Завершить задачу навсегда (failed), без повторов."""


class ResourceError(QueueTaskError):
    """Недостаточно ресурса — отложить (postpone)."""

    account_id: int | None
    op_code: str | None

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        account_id: int | None = None,
        op_code: str | None = None,
    ) -> None:
        super().__init__(code, message)
        self.account_id = account_id
        self.op_code = op_code

    def postpone_reason(self) -> str:
        if self.account_id is not None and self.op_code:
            return f"{self.code}:{self.account_id}:{self.op_code}"
        if self.account_id is not None:
            return f"{self.code}:{self.account_id}"
        return self.code


def map_telethon_exception(exc: BaseException) -> QueueTaskError:
    """Маппинг raw Telethon-исключений через classify_telethon_error (E2)."""
    try:
        from discovery_api.session_health import classify_telethon_error
    except ImportError:
        return RetryableError(TRANSIENT, str(exc) or TRANSIENT)

    kind, seconds = classify_telethon_error(exc)
    message = str(exc) or kind
    if kind == "flood":
        return RetryableError(FLOOD_WAIT, message, retry_after_seconds=seconds or None)
    if kind == "banned":
        return PermanentError(ACCOUNT_BANNED, message)
    if kind == "transient":
        return RetryableError(TRANSIENT, message)
    return PermanentError(FATAL, message)


def map_clump_error_message(err: str) -> QueueTaskError:
    """Маппинг строки ошибки clump → typed error (E2)."""
    text = str(err).strip()
    if not text:
        return RetryableError(ErrorCode.CLUMP_ERROR, "empty clump error")

    normalized = text.lower().replace(" ", "")
    ban_markers = (
        "userdeactivated",
        "authkeyunregistered",
        "sessionrevoked",
        "phonenumberbanned",
        "banned",
        "deactivated",
        "unauthorized",
    )
    if any(marker in normalized for marker in ban_markers):
        return PermanentError(ACCOUNT_BANNED, text)

    try:
        from discovery_api.session_health import parse_flood_wait_seconds

        seconds = parse_flood_wait_seconds(text)
        if seconds is not None:
            return RetryableError(
                ErrorCode.FLOOD_WAIT,
                text,
                retry_after_seconds=seconds,
            )
    except ImportError:
        pass

    if "floodwait" in normalized:
        return RetryableError(ErrorCode.FLOOD_WAIT, text)

    lowered = text.lower()
    if any(
        marker in lowered
        for marker in (
            "userdeactivated",
            "authkeyunregistered",
            "phonenumberbanned",
            "sessionrevoked",
            "unauthorized",
        )
    ):
        return PermanentError(ErrorCode.BANNED, text)

    return RetryableError(ErrorCode.CLUMP_ERROR, text)


def map_telethon_exception(exc: BaseException) -> QueueTaskError:
    """E2: Telethon/сеть → typed error через classify_telethon_error."""
    try:
        from discovery_api.session_health import classify_telethon_error
    except ImportError:
        return PermanentError(FATAL, str(exc))

    kind, seconds = classify_telethon_error(exc)
    if kind == "flood":
        return RetryableError(
            FLOOD_WAIT,
            str(exc),
            retry_after_seconds=int(seconds or 0) or None,
        )
    if kind == "banned":
        return PermanentError(ACCOUNT_BANNED, str(exc))
    if kind == "transient":
        return RetryableError(TRANSIENT, str(exc))
    return PermanentError(FATAL, str(exc))
