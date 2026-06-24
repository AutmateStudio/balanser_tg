"""SQLite-хранилище метаданных Telegram-аккаунтов (реестр для админ-панели)."""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional

log = __import__("logging").getLogger(__name__)


def _get_db_path() -> str:
    raw = os.getenv("ACCOUNT_STORE_PATH", "").strip()
    if raw:
        return raw
    base = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "telegram_accounts.db")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def init_account_db() -> None:
    path = _get_db_path()
    _ensure_parent_dir(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_accounts (
                session_name TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                max_channels INTEGER,
                admin_blocked INTEGER NOT NULL DEFAULT 0,
                block_reason TEXT,
                source TEXT NOT NULL DEFAULT 'import',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()


def reset_account_db_for_tests() -> None:
    path = _get_db_path()
    if os.path.isfile(path):
        os.remove(path)
    init_account_db()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["admin_blocked"] = bool(d.get("admin_blocked"))
    if d.get("max_channels") is not None:
        d["max_channels"] = int(d["max_channels"])
    return d


def upsert_account(
    session_name: str,
    *,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    max_channels: Optional[int] = None,
    admin_blocked: Optional[bool] = None,
    block_reason: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    init_account_db()
    path = _get_db_path()
    existing = get_account(session_name)
    dn = display_name if display_name is not None else (existing or {}).get("display_name", "")
    desc = description if description is not None else (existing or {}).get("description", "")
    mc = max_channels if max_channels is not None else (existing or {}).get("max_channels")
    blocked = (
        int(admin_blocked)
        if admin_blocked is not None
        else int((existing or {}).get("admin_blocked", False))
    )
    br = block_reason if block_reason is not None else (existing or {}).get("block_reason")
    src = source or (existing or {}).get("source") or "import"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO telegram_accounts (
                session_name, display_name, description, max_channels,
                admin_blocked, block_reason, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(session_name) DO UPDATE SET
                display_name=excluded.display_name,
                description=excluded.description,
                max_channels=excluded.max_channels,
                admin_blocked=excluded.admin_blocked,
                block_reason=excluded.block_reason,
                source=CASE
                    WHEN excluded.source != 'import' THEN excluded.source
                    ELSE telegram_accounts.source
                END,
                updated_at=datetime('now')
            """,
            (session_name, dn, desc, mc, blocked, br, src),
        )
        conn.commit()
    rec = get_account(session_name)
    assert rec is not None
    return rec


def get_account(session_name: str) -> Optional[Dict[str, Any]]:
    init_account_db()
    path = _get_db_path()
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM telegram_accounts WHERE session_name = ?",
            (session_name,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_accounts() -> List[Dict[str, Any]]:
    init_account_db()
    path = _get_db_path()
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM telegram_accounts ORDER BY session_name"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_account(session_name: str) -> bool:
    init_account_db()
    path = _get_db_path()
    with sqlite3.connect(path) as conn:
        cur = conn.execute(
            "DELETE FROM telegram_accounts WHERE session_name = ?",
            (session_name,),
        )
        conn.commit()
        return cur.rowcount > 0


def set_admin_blocked(
    session_name: str, *, blocked: bool, reason: Optional[str] = None
) -> Dict[str, Any]:
    return upsert_account(
        session_name,
        admin_blocked=blocked,
        block_reason=reason if blocked else None,
    )


def update_account_fields(
    session_name: str,
    *,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    max_channels: Optional[int] = None,
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    if display_name is not None:
        kwargs["display_name"] = display_name
    if description is not None:
        kwargs["description"] = description
    if max_channels is not None:
        kwargs["max_channels"] = max_channels
    return upsert_account(session_name, **kwargs)
