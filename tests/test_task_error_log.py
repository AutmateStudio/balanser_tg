"""Тесты структурированного логирования ошибок задач."""

from __future__ import annotations

import logging

import pytest

from app_balance.queue.error_codes import ErrorCode
from app_balance.queue.errors import PermanentError, ResourceError, RetryableError
from app_balance.queue.task_error_log import (
    bind_task_error_context,
    clear_task_error_context,
    format_task_error_line,
    format_task_ok_line,
    format_task_postpone_line,
    log_queue_task_error,
    log_task_error,
    log_task_ok,
    log_task_postpone,
)


@pytest.fixture(autouse=True)
def _reset_task_error_context() -> None:
    clear_task_error_context()
    yield
    clear_task_error_context()


def test_format_task_error_line_join_pending() -> None:
    line = format_task_error_line(
        "Нет доступа к чату для прослушивания «Chat» (ref=@ch): не участник",
        account="Client1",
        task_id=8882,
        error_type="RETRYABLE",
        is_flood=False,
        flood_wait_seconds=0,
    )
    assert line.startswith("ERROR: ACCOUNT - Client1 TASK_ID - 8882 TYPE - RETRYABLE")
    assert "IS_FLOOD - FALSE FLOOD_WAIT-0 |" in line
    assert "не участник" in line


def test_format_task_error_line_flood_wait() -> None:
    line = format_task_error_line(
        "FloodWait 269s при resolve '@tadviser'",
        account="Test2",
        task_id=14155,
        error_type="RETRYABLE",
        is_flood=True,
        flood_wait_seconds=269,
    )
    assert "IS_FLOOD - TRUE FLOOD_WAIT-269" in line


def test_log_queue_task_error_detects_flood_from_retryable() -> None:
    records: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger("test.task_error_log.flood")
    logger.handlers.clear()
    logger.addHandler(_Handler())
    logger.setLevel(logging.ERROR)

    exc = RetryableError(
        ErrorCode.FLOOD_WAIT,
        "FloodWait 42s при resolve '@x'",
        retry_after_seconds=42,
    )
    log_queue_task_error(logger, exc, task_id=1, account="Acc")

    assert len(records) == 1
    assert "TYPE - RETRYABLE" in records[0]
    assert "IS_FLOOD - TRUE FLOOD_WAIT-42" in records[0]


def test_log_task_error_skip_if_queued() -> None:
    records: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger("test.task_error_log.skip")
    logger.handlers.clear()
    logger.addHandler(_Handler())
    logger.setLevel(logging.ERROR)

    bind_task_error_context(task_id=99, account="Acc")
    log_task_error(
        logger,
        "parser resolve skip (no access) ref=@x: msg",
        error_type="RETRYABLE",
        skip_if_queued=True,
    )

    assert records == []


def test_error_type_labels() -> None:
    records: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger("test.task_error_log.types")
    logger.handlers.clear()
    logger.addHandler(_Handler())
    logger.setLevel(logging.ERROR)

    log_queue_task_error(
        logger,
        PermanentError(ErrorCode.CHANNEL_PRIVATE, "нет чата обсуждений"),
        task_id=3,
        account="A",
    )
    log_queue_task_error(
        logger,
        ResourceError(ErrorCode.INSUFFICIENT_RESOURCE, "rph exhausted"),
        task_id=4,
        account="A",
    )

    assert "TYPE - FATAL" in records[0]
    assert "TYPE - RESOURCE" in records[1]


def test_format_task_ok_line() -> None:
    line = format_task_ok_line(
        16589,
        "Добавить канал на parser-сессию",
        "/app/sessions/Client1",
    )
    assert line == "OK 16589 Добавить канал на parser-сессию Client1"


def test_format_task_postpone_line() -> None:
    line = format_task_postpone_line(
        16486,
        "Добавить канал на parser-сессию",
        "Test2",
        300,
    )
    assert line == "POSTPONE 16486 Добавить канал на parser-сессию Test2 300"


def test_log_task_ok_and_postpone_at_info(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.task_error_log.ok_postpone")
    with caplog.at_level(logging.INFO, logger="test.task_error_log.ok_postpone"):
        log_task_ok(logger, 1, "move_channel", "Client1")
        log_task_postpone(logger, 2, "move_channel", None, 300)

    assert format_task_ok_line(1, "move_channel", "Client1") in caplog.text
    assert format_task_postpone_line(2, "move_channel", None, 300) in caplog.text
