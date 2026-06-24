"""FIFO-очередь тяжёлых операций парсера (bulk add/remove каналов)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

_worker_task: asyncio.Task[None] | None = None
_handler: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None


def _db_path() -> str:
    raw = os.getenv("ACTION_QUEUE_DB", "").strip()
    if raw:
        return raw
    base = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "action_queue.db")


def init_action_queue_db() -> None:
    path = _db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_queue (
                id TEXT PRIMARY KEY,
                action_type TEXT NOT NULL,
                parser_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                progress_done INTEGER NOT NULL DEFAULT 0,
                progress_total INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
        conn.commit()


def reset_action_queue_for_tests() -> None:
    path = _db_path()
    if os.path.isfile(path):
        os.remove(path)
    init_action_queue_db()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["payload"] = json.loads(d.get("payload") or "{}")
    d["progress"] = {
        "done": int(d.pop("progress_done", 0)),
        "total": int(d.pop("progress_total", 0)),
    }
    return d


def enqueue_action(
    *,
    action_type: str,
    parser_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    init_action_queue_db()
    action_id = uuid.uuid4().hex
    total = len(payload.get("channel_list") or [])
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO action_queue (
                id, action_type, parser_id, payload, status, progress_total
            ) VALUES (?, ?, ?, ?, 'queued', ?)
            """,
            (action_id, action_type, parser_id, json.dumps(payload), total),
        )
        conn.commit()
    return get_action(action_id) or {}


def get_action(action_id: str) -> Optional[Dict[str, Any]]:
    init_action_queue_db()
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM action_queue WHERE id = ?", (action_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_actions(
    *,
    status: Optional[str] = None,
    parser_id: Optional[str] = None,
    action_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    init_action_queue_db()
    clauses: List[str] = []
    params: List[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if parser_id:
        clauses.append("parser_id = ?")
        params.append(parser_id)
    if action_type:
        clauses.append("action_type = ?")
        params.append(action_type)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(max(1, min(500, limit)))
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM action_queue{where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _update_action(action_id: str, **fields: Any) -> None:
    allowed = {
        "status", "progress_done", "progress_total", "error", "started_at", "finished_at"
    }
    sets: List[str] = []
    vals: List[Any] = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return
    vals.append(action_id)
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            f"UPDATE action_queue SET {', '.join(sets)} WHERE id = ?",
            vals,
        )
        conn.commit()


def _fetch_next_queued() -> Optional[Dict[str, Any]]:
    init_action_queue_db()
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT * FROM action_queue
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
    return _row_to_dict(row) if row else None


async def _worker_loop() -> None:
    while True:
        item = _fetch_next_queued()
        if item is None:
            await asyncio.sleep(0.5)
            continue
        action_id = item["id"]
        _update_action(action_id, status="running", started_at=_now_sql())
        try:
            if _handler is None:
                raise RuntimeError("action queue handler not registered")
            await _handler(item)
            _update_action(action_id, status="done", finished_at=_now_sql())
        except Exception as exc:
            log.exception("action queue failed id=%s", action_id)
            _update_action(
                action_id,
                status="failed",
                error=str(exc),
                finished_at=_now_sql(),
            )


def _now_sql() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def register_action_handler(
    handler: Callable[[Dict[str, Any]], Awaitable[None]],
) -> None:
    global _handler
    _handler = handler


def start_action_worker() -> None:
    global _worker_task
    init_action_queue_db()
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_worker_loop(), name="action-queue-worker")


async def stop_action_worker() -> None:
    global _worker_task
    task = _worker_task
    _worker_task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def update_action_progress(action_id: str, done: int, total: Optional[int] = None) -> None:
    fields: Dict[str, Any] = {"progress_done": done}
    if total is not None:
        fields["progress_total"] = total
    _update_action(action_id, **fields)
