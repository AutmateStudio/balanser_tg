"""Unit-тесты логики D12 E2E (D8 ответ API, channel match, B9 helpers)."""
from __future__ import annotations

import sys
from pathlib import Path

_E2E_DIR = Path(__file__).resolve().parents[1] / "scripts" / "e2e_d12"
if str(_E2E_DIR) not in sys.path:
    sys.path.insert(0, str(_E2E_DIR))

from e2e_lib import (  # noqa: E402
    channel_in_list,
    validate_d8_enqueue_response,
)


def test_channel_in_list_exact() -> None:
    assert channel_in_list("@foo", {"channel_list": ["@foo", "@bar"]})


def test_channel_in_list_case_insensitive() -> None:
    assert channel_in_list("@Foo", {"channel_list": ["@foo"]})


def test_channel_in_list_chat_id() -> None:
    assert channel_in_list(
        "-100123",
        {"channel_list": [], "allowed_chat_ids": [-100123, -100456]},
    )


def test_channel_in_list_missing() -> None:
    assert not channel_in_list("@missing", {"channel_list": ["@other"]})


def test_validate_d8_enqueue_response_ok() -> None:
    body = {
        "async_mode": True,
        "action_id": "a" * 32,
        "task_ids": [101],
    }
    assert validate_d8_enqueue_response(body) == []


def test_validate_d8_enqueue_response_legacy_async_mode_false() -> None:
    body = {
        "async_mode": False,
        "action_id": "sqlite-action",
        "task_ids": [],
    }
    errors = validate_d8_enqueue_response(body)
    assert any("async_mode" in e for e in errors)
    assert any("task_ids" in e for e in errors)


def test_validate_d8_enqueue_response_bad_action_id() -> None:
    body = {"async_mode": True, "action_id": "short", "task_ids": [1]}
    errors = validate_d8_enqueue_response(body)
    assert any("action_id" in e for e in errors)


def test_validate_d8_enqueue_response_empty_task_ids() -> None:
    body = {"async_mode": True, "action_id": "b" * 32, "task_ids": []}
    errors = validate_d8_enqueue_response(body)
    assert any("task_ids" in e for e in errors)
