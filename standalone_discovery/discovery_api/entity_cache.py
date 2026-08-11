from __future__ import annotations

import os
import random
import sqlite3
from typing import Dict, Iterable, Optional

# TTL записей entity_cache (дни). По умолчанию 7.
_DEFAULT_TTL_DAYS = 7

# Инцидент 2026-08-11: при массовом (не постепенном) старте ~48 сессий сразу
# после восстановления clump'ов каждая сессия резолвит свои каналы и дёргает
# get_cached_chat_ids/set_cached_chat_id почти одновременно. init_entity_cache_db
# раньше делал DELETE (TTL-чистку) + commit НА КАЖДЫЙ вызов — это writer-лок на
# shared SQLite-файл при любом обращении, не только когда чистка реально нужна.
# Приконкурентности x48 это гарантированно давало sqlite3.OperationalError:
# database is locked и "Listener сессии ... упал (fatal)" по всей пачке сразу.
# Фикс: WAL + busy_timeout (снимает большинство коротких коллизий сам SQLite)
# и вероятностная чистка (TTL 7 дней — нет смысла чистить на каждый вызов).
_CLEANUP_PROBABILITY = float(os.getenv("ENTITY_CACHE_CLEANUP_PROBABILITY", "0.005"))
_BUSY_TIMEOUT_SECONDS = float(os.getenv("ENTITY_CACHE_BUSY_TIMEOUT_SECONDS", "10"))


def _get_db_path() -> str:
    base_dir = os.path.join(os.path.dirname(__file__), "data")
    return os.path.join(base_dir, "entity_cache.db")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _ttl_days() -> int:
    raw = os.getenv("ENTITY_CACHE_TTL_DAYS", str(_DEFAULT_TTL_DAYS)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_TTL_DAYS


def _connect(path: str) -> sqlite3.Connection:
    """sqlite3.connect с WAL и увеличенным busy_timeout — под конкурентный доступ
    из многих сессий одновременно (см. заметку про инцидент 2026-08-11 выше)."""
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_SECONDS)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={int(_BUSY_TIMEOUT_SECONDS * 1000)}")
    return conn


def init_entity_cache_db(db_path: Optional[str] = None, *, force_cleanup: bool = False) -> str:
    path = db_path or _get_db_path()
    _ensure_parent_dir(path)
    with _connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_cache (
                username TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                resolved_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()
        # TTL 7 дней — чистить на каждый вызов избыточно и создаёт лишний
        # writer-лок под конкурентной нагрузкой. Делаем это редко, по вероятности.
        if force_cleanup or random.random() < _CLEANUP_PROBABILITY:
            conn.execute(
                f"""
                DELETE FROM entity_cache
                WHERE resolved_at < datetime('now', '-{_ttl_days()} days')
                """
            )
            conn.commit()
    return path


def get_cached_chat_ids(
    usernames: Iterable[str], *, db_path: Optional[str] = None
) -> Dict[str, int]:
    names = [u.strip().lstrip("@") for u in usernames if (u or "").strip().lstrip("@")]
    if not names:
        return {}

    path = init_entity_cache_db(db_path)
    placeholders = ",".join("?" for _ in names)
    ttl = _ttl_days()
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"""
            SELECT username, chat_id FROM entity_cache
            WHERE username IN ({placeholders})
              AND resolved_at >= datetime('now', '-{ttl} days')
            """,
            tuple(names),
        )
        rows = cur.fetchall()
        cur.close()

    out: Dict[str, int] = {}
    for row in rows:
        out[str(row["username"])] = int(row["chat_id"])
    return out


def set_cached_chat_id(username: str, chat_id: int, *, db_path: Optional[str] = None) -> None:
    normalized = (username or "").strip().lstrip("@")
    if not normalized:
        return

    path = init_entity_cache_db(db_path)
    with _connect(path) as conn:
        cur = conn.execute(
            """
            INSERT INTO entity_cache (username, chat_id, resolved_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(username) DO UPDATE SET
                chat_id=excluded.chat_id,
                resolved_at=datetime('now')
            """,
            (normalized, int(chat_id)),
        )
        cur.close()
        conn.commit()
