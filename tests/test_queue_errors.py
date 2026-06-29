"""E1 — unit-тесты typed errors и map_clump_error_message."""
from __future__ import annotations

import pytest

import telethon.errors as te

from app_balance.queue.errors import (
    ACCOUNT_BANNED,
    FLOOD_WAIT,
    FATAL,
    TRANSIENT,
    PermanentError,
    ResourceError,
    RetryableError,
    map_clump_error_message,
    map_telethon_exception,
)


def test_retryable_error_has_code_and_optional_delay() -> None:
    err = RetryableError("flood_wait", "FloodWait 10s", retry_after_seconds=10)
    assert err.code == "flood_wait"
    assert err.retry_after_seconds == 10
    assert str(err) == "FloodWait 10s"


def test_permanent_error_is_queue_task_error() -> None:
    err = PermanentError("invalid_payload", "missing parser_id")
    assert err.code == "invalid_payload"
    assert isinstance(err, PermanentError)


def test_resource_error_postpone_reason() -> None:
    err = ResourceError(
        "insufficient_resource",
        account_id=42,
        op_code="get_entity",
    )
    assert err.postpone_reason() == "insufficient_resource:42:get_entity"


def test_map_clump_error_flood_wait_string() -> None:
    mapped = map_clump_error_message("FloodWait 42s при resolve '@ch'")
    assert isinstance(mapped, RetryableError)
    assert mapped.code == "flood_wait"
    assert mapped.retry_after_seconds == 42


def test_map_clump_error_generic() -> None:
    mapped = map_clump_error_message("unexpected_owner")
    assert isinstance(mapped, RetryableError)
    assert mapped.code == "clump_error"


def test_map_clump_error_ban_string() -> None:
    mapped = map_clump_error_message("UserDeactivatedError: account blocked")
    assert isinstance(mapped, PermanentError)
    assert mapped.code == ACCOUNT_BANNED


def test_map_clump_error_no_discussion_is_permanent() -> None:
    msg = "У канала «Test» нет чата обсуждений — прослушивание невозможно"
    mapped = map_clump_error_message(msg)
    assert isinstance(mapped, PermanentError)
    assert mapped.code == "channel_private"


def test_map_clump_error_join_pending_retries_in_30_min() -> None:
    msg = (
        "Нет доступа к чату для прослушивания «Chat» "
        "(ref=@ch, listen_peer_id=-1001): не участник"
    )
    mapped = map_clump_error_message(msg)
    assert isinstance(mapped, RetryableError)
    assert mapped.code == "join_pending"
    assert mapped.retry_after_seconds == 1800


def test_map_telethon_exception_flood_wait() -> None:
    exc = te.FloodWaitError(None)
    exc.seconds = 12
    mapped = map_telethon_exception(exc)
    assert isinstance(mapped, RetryableError)
    assert mapped.code == FLOOD_WAIT
    assert mapped.retry_after_seconds == 12


def test_map_telethon_exception_banned() -> None:
    mapped = map_telethon_exception(te.UserDeactivatedError(None))
    assert isinstance(mapped, PermanentError)
    assert mapped.code == ACCOUNT_BANNED


def test_map_telethon_exception_transient() -> None:
    mapped = map_telethon_exception(ConnectionError("network down"))
    assert isinstance(mapped, RetryableError)
    assert mapped.code == TRANSIENT


def test_map_telethon_exception_fatal() -> None:
    mapped = map_telethon_exception(RuntimeError("boom"))
    assert isinstance(mapped, PermanentError)
    assert mapped.code == FATAL
