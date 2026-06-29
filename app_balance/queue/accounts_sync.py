"""A10 — sync accounts: SQLite account_store + SESSIONS_DIR + clump → PG accounts."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Any

from app_balance.queue import db

log = logging.getLogger(__name__)

_RUNTIME_STATUSES = frozenset({"cooldown", "banned", "error"})


@dataclass(frozen=True, slots=True)
class SyncConfig:
    account_store_path: str
    sessions_dir: str
    parser_store_path: str


@dataclass(frozen=True, slots=True)
class SyncResult:
    inserted: int
    updated: int
    unchanged: int
    total: int


@dataclass(frozen=True, slots=True)
class SqliteAccount:
    session_name: str
    admin_blocked: bool


@dataclass(frozen=True, slots=True)
class DesiredAccount:
    session_name: str
    admin_blocked: bool
    in_clump: bool
    status: str
    is_enabled: bool


@dataclass(frozen=True, slots=True)
class ExistingAccountRow:
    session_name: str
    status: str
    is_enabled: bool


def normalize_session_name(session_name: str) -> str:
    """Каноническое имя аккаунта (basename без .session)."""
    base = (session_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    if base.endswith(".session"):
        base = base[: -len(".session")]
    return base


def default_account_store_path() -> str:
    raw = os.getenv("ACCOUNT_STORE_PATH", "").strip()
    if raw:
        return raw
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    return os.path.join(
        repo_root,
        "standalone_discovery",
        "discovery_api",
        "data",
        "telegram_accounts.db",
    )


def default_sessions_dir() -> str:
    return os.getenv("SESSIONS_DIR", "/app/sessions").strip() or "/app/sessions"


def default_parser_store_path() -> str:
    raw = os.getenv("PARSER_STORE_PATH", "").strip()
    if raw:
        return raw
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    return os.path.join(
        repo_root,
        "standalone_discovery",
        "discovery_api",
        "data",
        "parser_jobs.json",
    )


def sync_config_from_env() -> SyncConfig:
    return SyncConfig(
        account_store_path=default_account_store_path(),
        sessions_dir=default_sessions_dir(),
        parser_store_path=default_parser_store_path(),
    )


def load_sqlite_accounts(path: str) -> dict[str, SqliteAccount]:
    """Читает telegram_accounts из SQLite; пустой dict если файла нет."""
    if not os.path.isfile(path):
        return {}
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT session_name, admin_blocked FROM telegram_accounts"
        ).fetchall()
    out: dict[str, SqliteAccount] = {}
    for row in rows:
        name = normalize_session_name(str(row["session_name"]))
        if not name:
            continue
        out[name] = SqliteAccount(
            session_name=name,
            admin_blocked=bool(row["admin_blocked"]),
        )
    return out


def scan_sessions_dir(path: str) -> set[str]:
    """Имена аккаунтов по файлам *.session в SESSIONS_DIR."""
    if not os.path.isdir(path):
        return set()
    out: set[str] = set()
    for entry in os.listdir(path):
        if entry.endswith(".session"):
            name = normalize_session_name(entry)
            if name:
                out.add(name)
    return out


def _normalize_persisted_record(rec: dict[str, Any]) -> dict[str, Any]:
    out = dict(rec)
    session_list = out.get("session_name_list")
    legacy_name = out.get("session_name")
    if not isinstance(session_list, list) or not session_list:
        if legacy_name:
            out["session_name_list"] = [str(legacy_name)]
        else:
            out["session_name_list"] = []
    else:
        out["session_name_list"] = [str(x) for x in session_list]
    return out


def load_clump_sessions(parser_path: str) -> set[str]:
    """session_name из parser_jobs.json (schema v2 + legacy session_name)."""
    if not os.path.isfile(parser_path):
        return set()
    try:
        with open(parser_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Не удалось прочитать %s: %s", parser_path, exc)
        return set()
    if not isinstance(data, list):
        return set()
    out: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        rec = _normalize_persisted_record(item)
        for raw_name in rec.get("session_name_list") or []:
            name = normalize_session_name(str(raw_name))
            if name:
                out.add(name)
    return out


def resolve_status(
    *,
    admin_blocked: bool,
    in_clump: bool,
    existing_status: str | None = None,
) -> tuple[str, bool]:
    """Целевой status/is_enabled для новой строки или до применения runtime-guard."""
    if admin_blocked:
        return "disabled", False
    if not in_clump:
        return "disabled", False
    if existing_status in _RUNTIME_STATUSES:
        return existing_status, existing_status != "disabled"
    return "active", True


def build_desired_rows(
    sqlite_accounts: dict[str, SqliteAccount],
    disk_sessions: set[str],
    clump_sessions: set[str],
    existing_by_name: dict[str, ExistingAccountRow] | None = None,
) -> list[DesiredAccount]:
    """Union SQLite ∪ disk → desired rows с resolved status."""
    existing_by_name = existing_by_name or {}
    all_names = sorted(set(sqlite_accounts) | disk_sessions)
    rows: list[DesiredAccount] = []
    for name in all_names:
        sqlite_row = sqlite_accounts.get(name)
        admin_blocked = sqlite_row.admin_blocked if sqlite_row else False
        in_clump = name in clump_sessions
        existing = existing_by_name.get(name)
        existing_status = existing.status if existing else None
        status, is_enabled = resolve_status(
            admin_blocked=admin_blocked,
            in_clump=in_clump,
            existing_status=existing_status,
        )
        rows.append(
            DesiredAccount(
                session_name=name,
                admin_blocked=admin_blocked,
                in_clump=in_clump,
                status=status,
                is_enabled=is_enabled,
            )
        )
    return rows


_UPSERT_SQL = """
INSERT INTO accounts (session_name, status, is_enabled, updated_at)
VALUES ($1, $2::account_status, $3, now())
ON CONFLICT (session_name) DO UPDATE SET
  status = CASE
    WHEN $4 THEN 'disabled'::account_status
    WHEN accounts.status IN ('cooldown', 'banned', 'error') THEN accounts.status
    ELSE EXCLUDED.status
  END,
  is_enabled = CASE
    WHEN $4 THEN false
    WHEN accounts.status IN ('cooldown', 'banned', 'error') THEN accounts.is_enabled
    ELSE EXCLUDED.is_enabled
  END,
  updated_at = now()
RETURNING (xmax = 0) AS inserted
"""


async def _load_existing(conn, session_names: list[str]) -> dict[str, ExistingAccountRow]:
    if not session_names:
        return {}
    rows = await conn.fetch(
        """
        SELECT session_name, status, is_enabled
        FROM accounts
        WHERE session_name = ANY($1::text[])
        """,
        session_names,
    )
    return {
        str(row["session_name"]): ExistingAccountRow(
            session_name=str(row["session_name"]),
            status=str(row["status"]),
            is_enabled=bool(row["is_enabled"]),
        )
        for row in rows
    }


def _row_changed(existing: ExistingAccountRow | None, desired: DesiredAccount) -> bool:
    if existing is None:
        return True
    if desired.admin_blocked:
        return existing.status != "disabled" or existing.is_enabled is not False
    if existing.status in _RUNTIME_STATUSES:
        return False
    return existing.status != desired.status or existing.is_enabled != desired.is_enabled


async def sync_accounts_to_pg(
    config: SyncConfig,
    *,
    dry_run: bool = False,
) -> SyncResult:
    """Идемпотентный upsert accounts из discovery-источников в PG."""
    sqlite_accounts = load_sqlite_accounts(config.account_store_path)
    disk_sessions = scan_sessions_dir(config.sessions_dir)
    clump_sessions = load_clump_sessions(config.parser_store_path)

    await db.init_pool()

    async with db.transaction() as conn:
        pre_names = sorted(set(sqlite_accounts) | disk_sessions)
        existing_before = await _load_existing(conn, pre_names)
        desired_rows = build_desired_rows(
            sqlite_accounts,
            disk_sessions,
            clump_sessions,
            existing_before,
        )

        inserted = 0
        updated = 0
        unchanged = 0

        for row in desired_rows:
            existing = existing_before.get(row.session_name)
            will_change = _row_changed(existing, row)

            if dry_run:
                if existing is None:
                    inserted += 1
                elif will_change:
                    updated += 1
                else:
                    unchanged += 1
                continue

            if existing is not None and not will_change:
                unchanged += 1
                continue

            was_insert = await conn.fetchval(
                _UPSERT_SQL,
                row.session_name,
                row.status,
                row.is_enabled,
                row.admin_blocked,
            )
            if was_insert:
                inserted += 1
            elif will_change:
                updated += 1
            else:
                unchanged += 1

    total = len(desired_rows)
    log.info(
        "sync accounts: total=%s inserted=%s updated=%s unchanged=%s dry_run=%s",
        total,
        inserted,
        updated,
        unchanged,
        dry_run,
    )
    return SyncResult(
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
        total=total,
    )


def pg_sync_enabled() -> bool:
    """True, если задан QUEUE_DATABASE_URL (A10 / D6)."""
    return bool(os.getenv("QUEUE_DATABASE_URL", "").strip())


async def sync_accounts_to_pg_best_effort(*, context: str = "") -> SyncResult | None:
    """A10 после QR: upsert accounts в PG; ошибки PG не пробрасывает.

    Вызывается из discovery_api после успешного QR, чтобы новый аккаунт
    сразу появился в queue/metrics и был доступен dispatch.
    """
    if not pg_sync_enabled():
        log.debug(
            "sync accounts (%s): пропуск — QUEUE_DATABASE_URL не задан",
            context or "trigger",
        )
        return None
    try:
        result = await sync_accounts_to_pg(sync_config_from_env())
        log.info(
            "sync accounts (%s): total=%s inserted=%s updated=%s unchanged=%s",
            context or "trigger",
            result.total,
            result.inserted,
            result.updated,
            result.unchanged,
        )
        return result
    except Exception:  # noqa: BLE001 — QR не должен падать из-за PG
        log.exception(
            "sync accounts (%s): ошибка PG, операция discovery не прервана",
            context or "trigger",
        )
        return None
