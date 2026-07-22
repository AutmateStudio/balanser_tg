"""Обратимая «подмена» очереди: backup → cancel → restore."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

from .config import PAUSE_ERROR_PREFIX
from .db import PAUSEABLE_STATUSES

log = logging.getLogger("throughput.queue_swap")

ACTIVE_FOR_DEDUP = ("queued", "scheduled", "retry", "in_progress")


def parse_run_after(value: str | datetime | None) -> datetime | None:
    """asyncpg требует datetime, не ISO-строку из JSON-бэкапа."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class BackupTask:
    id: int
    status: str
    run_after: str | None
    dedup_key: str | None
    task_type_code: str
    priority: int | None = None
    account_id: int | None = None
    last_error: str | None = None


def pause_marker(run_id: str) -> str:
    return f"{PAUSE_ERROR_PREFIX}{run_id}"


def serialize_row(row: asyncpg.Record) -> BackupTask:
    run_after = row["run_after"]
    return BackupTask(
        id=int(row["id"]),
        status=str(row["status"]),
        run_after=run_after.isoformat() if run_after is not None else None,
        dedup_key=row["dedup_key"],
        task_type_code=str(row["task_type_code"]),
        priority=int(row["priority"]) if row["priority"] is not None else None,
        account_id=int(row["account_id"]) if row["account_id"] is not None else None,
        last_error=row["last_error"],
    )


async def backup_pauseable_tasks(pool: asyncpg.Pool) -> list[BackupTask]:
    rows = await pool.fetch(
        """
        SELECT id, status::text AS status, run_after, dedup_key,
               task_type_code, priority, account_id, last_error
        FROM task_queue
        WHERE status = ANY($1::task_status[])
        ORDER BY id
        """,
        list(PAUSEABLE_STATUSES),
    )
    return [serialize_row(r) for r in rows]


def write_backup(path: Path, tasks: list[BackupTask], *, run_id: str) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "count": len(tasks),
        "tasks": [asdict(t) for t in tasks],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return payload


def load_backup(path: Path) -> list[BackupTask]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    tasks_raw = raw.get("tasks") if isinstance(raw, dict) else raw
    out: list[BackupTask] = []
    for item in tasks_raw or []:
        out.append(
            BackupTask(
                id=int(item["id"]),
                status=str(item["status"]),
                run_after=item.get("run_after"),
                dedup_key=item.get("dedup_key"),
                task_type_code=str(item["task_type_code"]),
                priority=item.get("priority"),
                account_id=item.get("account_id"),
                last_error=item.get("last_error"),
            )
        )
    return out


async def cancel_tasks(
    pool: asyncpg.Pool,
    task_ids: list[int],
    *,
    run_id: str,
) -> int:
    if not task_ids:
        return 0
    marker = pause_marker(run_id)
    rows = await pool.fetch(
        """
        UPDATE task_queue
        SET status = 'cancelled'::task_status,
            last_error = $2,
            last_error_at = now(),
            finished_at = COALESCE(finished_at, now()),
            locked_by = NULL,
            locked_at = NULL,
            locked_until = NULL,
            updated_at = now()
        WHERE id = ANY($1::bigint[])
          AND status = ANY($3::task_status[])
        RETURNING id
        """,
        task_ids,
        marker,
        list(PAUSEABLE_STATUSES),
    )
    return len(rows)


async def pause_queue(
    pool: asyncpg.Pool,
    backup_path: Path,
    *,
    run_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    tasks = await backup_pauseable_tasks(pool)
    write_backup(backup_path, tasks, run_id=run_id)
    if dry_run:
        log.info("dry-run: backup %s tasks → %s (без cancel)", len(tasks), backup_path)
        return {"backed_up": len(tasks), "cancelled": 0, "dry_run": True}
    cancelled = await cancel_tasks(pool, [t.id for t in tasks], run_id=run_id)
    log.info("queue pause: backed_up=%s cancelled=%s", len(tasks), cancelled)
    return {"backed_up": len(tasks), "cancelled": cancelled, "dry_run": False}


def can_restore_status(status: str) -> bool:
    return status in PAUSEABLE_STATUSES


async def _dedup_conflicts(
    pool: asyncpg.Pool,
    tasks: list[BackupTask],
) -> set[int]:
    """ID задач бэкапа, чей dedup_key уже занят другой активной задачей."""
    conflicted: set[int] = set()
    keys = [t.dedup_key for t in tasks if t.dedup_key]
    if not keys:
        return conflicted
    rows = await pool.fetch(
        """
        SELECT id, dedup_key
        FROM task_queue
        WHERE dedup_key = ANY($1::text[])
          AND status = ANY($2::task_status[])
        """,
        keys,
        list(ACTIVE_FOR_DEDUP),
    )
    active_by_key: dict[str, list[int]] = {}
    for r in rows:
        key = r["dedup_key"]
        if key:
            active_by_key.setdefault(key, []).append(int(r["id"]))

    backup_ids = {t.id for t in tasks}
    for t in tasks:
        if not t.dedup_key:
            continue
        holders = active_by_key.get(t.dedup_key) or []
        # конфликт, если активна задача с тем же ключом, но это НЕ сама восстанавливаемая
        if any(hid not in backup_ids for hid in holders):
            conflicted.add(t.id)
        # или если активна другая задача из бэкапа (не должна, но на всякий)
        elif any(hid != t.id for hid in holders):
            conflicted.add(t.id)
    return conflicted


async def restore_queue(
    pool: asyncpg.Pool,
    backup_path: Path,
    *,
    run_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not backup_path.is_file():
        return {
            "restored": 0,
            "skipped_conflict": 0,
            "skipped_not_cancelled": 0,
            "conflicts": [],
            "error": f"backup not found: {backup_path}",
        }

    tasks = load_backup(backup_path)
    marker = pause_marker(run_id)
    conflicts = await _dedup_conflicts(pool, tasks)
    restored = 0
    skipped_conflict = 0
    skipped_not_cancelled = 0
    conflict_details: list[dict[str, Any]] = []

    for t in tasks:
        if t.id in conflicts:
            skipped_conflict += 1
            conflict_details.append(
                {
                    "id": t.id,
                    "dedup_key": t.dedup_key,
                    "original_status": t.status,
                    "reason": "dedup_key_active",
                }
            )
            continue
        if not can_restore_status(t.status):
            skipped_not_cancelled += 1
            continue
        if dry_run:
            restored += 1
            continue

        row = await pool.fetchrow(
            """
            UPDATE task_queue
            SET status = $2::task_status,
                run_after = COALESCE($3::timestamptz, run_after),
                last_error = CASE
                    WHEN last_error = $4 THEN NULL
                    ELSE last_error
                END,
                finished_at = NULL,
                updated_at = now()
            WHERE id = $1
              AND status = 'cancelled'::task_status
              AND (
                    last_error = $4
                 OR last_error LIKE $5
              )
            RETURNING id
            """,
            t.id,
            t.status,
            parse_run_after(t.run_after),
            marker,
            f"{PAUSE_ERROR_PREFIX}%",
        )
        if row:
            restored += 1
        else:
            skipped_not_cancelled += 1

    result = {
        "restored": restored,
        "skipped_conflict": skipped_conflict,
        "skipped_not_cancelled": skipped_not_cancelled,
        "conflicts": conflict_details[:100],
        "backup_count": len(tasks),
        "dry_run": dry_run,
    }
    log.info("queue restore: %s", result)
    return result


# --- чистые хелперы для юнит-тестов (без PG) ---


def plan_restore(
    backup: list[BackupTask],
    *,
    active_dedup_holders: dict[str, list[int]],
) -> dict[str, Any]:
    """Спланировать restore без БД: какие id восстановятся / конфликтуют."""
    restore_ids: list[int] = []
    conflicts: list[dict[str, Any]] = []
    for t in backup:
        if not can_restore_status(t.status):
            continue
        if t.dedup_key:
            holders = active_dedup_holders.get(t.dedup_key) or []
            foreign = [h for h in holders if h != t.id]
            if foreign:
                conflicts.append(
                    {
                        "id": t.id,
                        "dedup_key": t.dedup_key,
                        "holders": foreign,
                    }
                )
                continue
        restore_ids.append(t.id)
    return {
        "restore_ids": restore_ids,
        "conflicts": conflicts,
        "restored": len(restore_ids),
        "skipped_conflict": len(conflicts),
    }
