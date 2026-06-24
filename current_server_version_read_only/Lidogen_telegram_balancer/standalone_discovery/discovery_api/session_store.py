from __future__ import annotations

import os
import sqlite3
from typing import Dict, List


def _get_db_path() -> str:
    db_path = os.getenv("DISCOVERY_SESSIONS_DB", "").strip()
    if db_path:
        return db_path
    return os.path.join(os.path.dirname(__file__), "data", "telegram_sessions.db")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def init_session_db() -> None:
    db_path = _get_db_path()
    _ensure_parent_dir(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_sessions (
                session_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                qr_url TEXT,
                phone TEXT,
                user_id INTEGER,
                user_name TEXT,
                session_string TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()


def upsert_session(
    *,
    session_id: str,
    status: str,
    qr_url: str = "",
    phone: str | None = None,
    user_id: int | None = None,
    user_name: str | None = None,
    session_string: str | None = None,
) -> None:
    db_path = _get_db_path()
    _ensure_parent_dir(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO telegram_sessions (
                session_id, status, qr_url, phone, user_id, user_name, session_string, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(session_id) DO UPDATE SET
                status=excluded.status,
                qr_url=excluded.qr_url,
                phone=excluded.phone,
                user_id=excluded.user_id,
                user_name=excluded.user_name,
                session_string=excluded.session_string,
                updated_at=datetime('now')
            """,
            (session_id, status, qr_url, phone, user_id, user_name, session_string),
        )
        conn.commit()


def delete_session(session_id: str) -> None:
    db_path = _get_db_path()
    _ensure_parent_dir(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM telegram_sessions WHERE session_id = ?", (session_id,))
        conn.commit()


def list_restorable_sessions() -> List[Dict]:
    db_path = _get_db_path()
    _ensure_parent_dir(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT session_id, status, qr_url, phone, user_id, user_name, session_string
            FROM telegram_sessions
            WHERE status = 'success' AND session_string IS NOT NULL AND session_string != ''
            """
        ).fetchall()
    return [dict(row) for row in rows]

