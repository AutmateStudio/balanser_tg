"""E5 — unit-тесты реестра кодов ошибок."""
from __future__ import annotations

from pathlib import Path

import pytest

from app_balance.queue.error_codes import ErrorCode, classify_exception_code, error_code_prefix
from app_balance.queue.errors import PermanentError, ResourceError, RetryableError


def test_error_code_prefix_exact_match() -> None:
    assert error_code_prefix("flood_wait") == "flood_wait"
    assert error_code_prefix("watchdog:task_timeout_exceeded") == "watchdog:task_timeout_exceeded"


def test_error_code_prefix_composite() -> None:
    assert error_code_prefix("insufficient_resource:42:get_entity") == "insufficient_resource"
    assert error_code_prefix("account_reserve_failed:99") == "account_reserve_failed"
    assert error_code_prefix("unknown_task_type:move_channel") == "unknown_task_type"
    assert error_code_prefix("no_ops_for_role:source") == "no_ops_for_role"


def test_error_code_prefix_none_and_empty() -> None:
    assert error_code_prefix(None) is None
    assert error_code_prefix("") is None
    assert error_code_prefix("   ") is None


def test_classify_exception_code_typed_errors() -> None:
    assert classify_exception_code(RetryableError("flood_wait")) == "flood_wait"
    assert classify_exception_code(PermanentError("invalid_payload")) == "invalid_payload"
    assert classify_exception_code(
        ResourceError("insufficient_resource", account_id=1, op_code="get_entity")
    ) == "insufficient_resource"


def test_classify_exception_code_timeout() -> None:
    assert classify_exception_code(TimeoutError()) == "transient_error"


def test_classify_exception_code_unexpected() -> None:
    assert classify_exception_code(ValueError("boom: detail")) == "unexpected_error"
    assert classify_exception_code(RuntimeError("something broke")) == "unexpected_error"


@pytest.mark.parametrize("code", list(ErrorCode))
def test_all_error_codes_are_snake_case(code: ErrorCode) -> None:
    value = code.value
    assert value == value.lower()
    assert " " not in value


_RUNBOOK_PATH = Path(__file__).resolve().parents[1] / "docs" / "queue-runbook.md"


def test_runbook_documents_all_error_codes() -> None:
    """E5 CI: каждый ErrorCode упомянут в docs/queue-runbook.md."""
    text = _RUNBOOK_PATH.read_text(encoding="utf-8")
    missing: list[str] = []
    for code in ErrorCode:
        value = code.value
        if f"`{value}`" in text or f"`{value}:" in text:
            continue
        missing.append(value)
    assert missing == [], f"В queue-runbook.md нет кодов: {', '.join(missing)}"
