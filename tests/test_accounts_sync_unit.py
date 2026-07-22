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


async def test_sync_best_effort_skips_without_dsn(monkeypatch) -> None:
    from app_balance.queue.accounts_sync import sync_accounts_to_pg_best_effort

    monkeypatch.delenv("QUEUE_DATABASE_URL", raising=False)
    assert await sync_accounts_to_pg_best_effort(context="test") is None


async def test_sync_best_effort_calls_sync_when_dsn_set(monkeypatch) -> None:
    from app_balance.queue import accounts_sync
    from app_balance.queue.accounts_sync import SyncResult, sync_accounts_to_pg_best_effort

    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/test")

    async def _fake_sync(_config, *, dry_run=False):
        return SyncResult(inserted=1, updated=0, unchanged=2, total=3)

    monkeypatch.setattr(accounts_sync, "sync_accounts_to_pg", _fake_sync)
    result = await sync_accounts_to_pg_best_effort(context="qr:Test2")
    assert result is not None
    assert result.inserted == 1
    assert result.total == 3


async def test_sync_best_effort_swallows_pg_errors(monkeypatch) -> None:
    from app_balance.queue import accounts_sync
    from app_balance.queue.accounts_sync import sync_accounts_to_pg_best_effort

    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/test")

    async def _fail(_config, *, dry_run=False):
        raise RuntimeError("pg down")

    monkeypatch.setattr(accounts_sync, "sync_accounts_to_pg", _fail)
    assert await sync_accounts_to_pg_best_effort(context="qr:Test2") is None


async def test_sync_best_effort_forwards_parser_store_path_override(monkeypatch) -> None:
    """Регресс fix/accounts-sync-parser-store-path: путь writer'а доходит до reader'а.

    Если override передан — синк должен читать membership именно из него,
    а не из захардкоженного репозиторного пути (который в контейнере не существует).
    """
    from app_balance.queue import accounts_sync
    from app_balance.queue.accounts_sync import sync_accounts_to_pg_best_effort

    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/test")

    captured: dict[str, str] = {}

    async def _capture_sync(config, *, dry_run=False):
        captured["parser_store_path"] = config.parser_store_path
        from app_balance.queue.accounts_sync import SyncResult

        return SyncResult(inserted=0, updated=0, unchanged=0, total=0)

    monkeypatch.setattr(accounts_sync, "sync_accounts_to_pg", _capture_sync)

    real_path = "/app/discovery_api/data/parser_jobs.json"
    await sync_accounts_to_pg_best_effort(
        context="enroll:Reriv8095", parser_store_path=real_path
    )
    assert captured["parser_store_path"] == real_path


def test_default_parser_store_path_prefers_env(monkeypatch) -> None:
    from app_balance.queue.accounts_sync import default_parser_store_path

    monkeypatch.setenv("PARSER_STORE_PATH", "/explicit/parser_jobs.json")
    assert default_parser_store_path() == "/explicit/parser_jobs.json"


def test_default_parser_store_path_uses_writer_resolver(monkeypatch) -> None:
    """Без env путь берётся из того же резолвера, что и писатель discovery_api."""
    import sys
    import types

    from app_balance.queue.accounts_sync import default_parser_store_path

    monkeypatch.delenv("PARSER_STORE_PATH", raising=False)

    fake_ps = types.ModuleType("discovery_api.parser_store")
    fake_ps._store_path = lambda: "/app/discovery_api/data/parser_jobs.json"  # type: ignore[attr-defined]
    fake_pkg = sys.modules.get("discovery_api") or types.ModuleType("discovery_api")
    monkeypatch.setitem(sys.modules, "discovery_api", fake_pkg)
    monkeypatch.setitem(sys.modules, "discovery_api.parser_store", fake_ps)

    assert default_parser_store_path() == "/app/discovery_api/data/parser_jobs.json"


async def test_sync_best_effort_uses_env_path_when_no_override(monkeypatch) -> None:
    from app_balance.queue import accounts_sync
    from app_balance.queue.accounts_sync import sync_accounts_to_pg_best_effort

    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/test")
    monkeypatch.setenv("PARSER_STORE_PATH", "/env/driven/parser_jobs.json")

    captured: dict[str, str] = {}

    async def _capture_sync(config, *, dry_run=False):
        captured["parser_store_path"] = config.parser_store_path
        from app_balance.queue.accounts_sync import SyncResult

        return SyncResult(inserted=0, updated=0, unchanged=0, total=0)

    monkeypatch.setattr(accounts_sync, "sync_accounts_to_pg", _capture_sync)

    await sync_accounts_to_pg_best_effort(context="enroll:Reriv8095")
    assert captured["parser_store_path"] == "/env/driven/parser_jobs.json"
