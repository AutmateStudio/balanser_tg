"""A10 — unit-тесты accounts_sync (без PG)."""
from __future__ import annotations

import json

from app_balance.queue.accounts_sync import (
    ExistingAccountRow,
    SqliteAccount,
    build_desired_rows,
    load_clump_sessions,
    normalize_session_name,
    resolve_status,
    scan_sessions_dir,
)


def test_normalize_session_name_strips_path_and_extension() -> None:
    assert normalize_session_name("/app/sessions/Client1.session") == "Client1"
    assert normalize_session_name("Client2.session") == "Client2"
    assert normalize_session_name(r"C:\sessions\Acc3.session") == "Acc3"


def test_resolve_status_admin_blocked_overrides_clump() -> None:
    status, enabled = resolve_status(admin_blocked=True, in_clump=True)
    assert status == "disabled"
    assert enabled is False


def test_resolve_status_not_in_clump_is_inactive() -> None:
    status, enabled = resolve_status(admin_blocked=False, in_clump=False)
    assert status == "disabled"
    assert enabled is False


def test_resolve_status_in_clump_is_active() -> None:
    status, enabled = resolve_status(admin_blocked=False, in_clump=True)
    assert status == "active"
    assert enabled is True


def test_resolve_status_preserves_runtime_cooldown() -> None:
    status, enabled = resolve_status(
        admin_blocked=False,
        in_clump=True,
        existing_status="cooldown",
    )
    assert status == "cooldown"
    assert enabled is True


def test_resolve_status_admin_blocked_overrides_cooldown() -> None:
    status, enabled = resolve_status(
        admin_blocked=True,
        in_clump=True,
        existing_status="cooldown",
    )
    assert status == "disabled"
    assert enabled is False


def test_build_desired_rows_union_sqlite_and_disk() -> None:
    sqlite = {
        "acc_a": SqliteAccount("acc_a", admin_blocked=False),
    }
    disk = {"acc_b"}
    clump = {"acc_a"}
    rows = build_desired_rows(sqlite, disk, clump)
    by_name = {r.session_name: r for r in rows}
    assert set(by_name) == {"acc_a", "acc_b"}
    assert by_name["acc_a"].status == "active"
    assert by_name["acc_b"].status == "disabled"


def test_build_desired_rows_respects_runtime_status_from_existing() -> None:
    existing = {
        "acc_cool": ExistingAccountRow("acc_cool", "cooldown", True),
    }
    rows = build_desired_rows(
        {"acc_cool": SqliteAccount("acc_cool", False)},
        set(),
        {"acc_cool"},
        existing,
    )
    assert len(rows) == 1
    assert rows[0].status == "cooldown"


def test_scan_sessions_dir(tmp_path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "One.session").write_text("", encoding="utf-8")
    (sessions / "Two.session").write_text("", encoding="utf-8")
    (sessions / "readme.txt").write_text("x", encoding="utf-8")
    assert scan_sessions_dir(str(sessions)) == {"One", "Two"}


def test_load_clump_sessions_v2_and_legacy(tmp_path) -> None:
    path = tmp_path / "parser_jobs.json"
    path.write_text(
        json.dumps(
            [
                {"parser_id": "p1", "session_name_list": ["Client1", "/app/sessions/Client2"]},
                {"parser_id": "p2", "session_name": "LegacyAcc"},
            ]
        ),
        encoding="utf-8",
    )
    assert load_clump_sessions(str(path)) == {"Client1", "Client2", "LegacyAcc"}


def test_load_clump_sessions_missing_file() -> None:
    assert load_clump_sessions("/nonexistent/parser_jobs.json") == set()
