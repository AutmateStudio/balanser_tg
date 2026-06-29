"""E5 — стабильные машиночитаемые коды last_error (ТЗ §27, мониторинг).

Единый реестр кодов для task_queue.last_error, task_attempts.error_code и D10 API.
"""
from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:  # Python < 3.11
    from enum import Enum

    class StrEnum(str, Enum):
        """Совместимость StrEnum для Python 3.10."""

        pass


class ErrorCode(StrEnum):
    """Каталог стабильных кодов ошибок очереди."""

    # --- retryable ---
    FLOOD_WAIT = "flood_wait"
    CLUMP_ERROR = "clump_error"
    CLUMP_NOT_LOADED = "clump_not_loaded"

    # --- permanent ---
    INVALID_PAYLOAD = "invalid_payload"
    ACCOUNT_NOT_FOUND = "account_not_found"
    UNSUPPORTED_TASK_TYPE = "unsupported_task_type"
    UNKNOWN_TASK_TYPE = "unknown_task_type"

    # --- resource / postpone ---
    INSUFFICIENT_RESOURCE = "insufficient_resource"
    MISSING_AVAILABILITY = "missing_availability"
    NO_AVAILABLE_ACCOUNT = "no_available_account"
    NO_OPS_FOR_ROLE = "no_ops_for_role"
    ACCOUNT_RESERVE_FAILED = "account_reserve_failed"
    DUAL_ACCOUNT_RESERVE_FAILED = "dual_account_reserve_failed"
    MISSING_DUAL_ACCOUNTS = "missing_dual_accounts"
    DUAL_ACCOUNTS_SAME_ID = "dual_accounts_same_id"

    # --- системные ---
    WATCHDOG_TASK_TIMEOUT = "watchdog:task_timeout_exceeded"
    UNEXPECTED_ERROR = "unexpected_error"

    # --- зарезервировано под E2 (Telethon) ---
    CHANNEL_PRIVATE = "channel_private"
    JOIN_PENDING = "join_pending"
    BANNED = "banned"
    PEER_FLOOD = "peer_flood"
    TRANSIENT_ERROR = "transient_error"


# Префиксы композитных причин (значение до первого «:» при суффиксе с деталями).
_COMPOSITE_PREFIXES = frozenset(
    {
        ErrorCode.INSUFFICIENT_RESOURCE,
        ErrorCode.NO_OPS_FOR_ROLE,
        ErrorCode.ACCOUNT_RESERVE_FAILED,
        ErrorCode.DUAL_ACCOUNT_RESERVE_FAILED,
        ErrorCode.UNKNOWN_TASK_TYPE,
        ErrorCode.ACCOUNT_NOT_FOUND,
        ErrorCode.CLUMP_NOT_LOADED,
    }
)


def error_code_prefix(value: str | None) -> str | None:
    """Извлекает стабильный код из last_error (полное значение или префикс до «:»)."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    for code in ErrorCode:
        if text == code.value:
            return code.value

    head, sep, _tail = text.partition(":")
    if sep and head in _COMPOSITE_PREFIXES:
        return head

    return head if sep else text


def normalize_error_code(code: str | ErrorCode | None) -> str:
    """Приводит код ошибки к строке (StrEnum → value)."""
    if code is None:
        return ErrorCode.UNEXPECTED_ERROR
    if isinstance(code, ErrorCode):
        return code.value
    return str(code)


def classify_exception_code(exc: BaseException) -> str:
    """Стабильный код для любого исключения без парсинга текста."""
    code = getattr(exc, "code", None)
    if code is not None and str(code).strip():
        return normalize_error_code(code)
    if isinstance(exc, TimeoutError):
        return ErrorCode.TRANSIENT_ERROR
    return ErrorCode.UNEXPECTED_ERROR
