#!/usr/bin/env python3
"""Убирает аккаунт из очереди и clump, не покидая его каналы.

Без --execute только печатает найденные id и session_name.
--execute разрешён, только если в PG ровно одна строка accounts.
Каналы в source_channels не меняются и LeaveChannel не вызывается.
.session переносится в каталог карантина, файл не удаляется.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _token(raw: str) -> str:
    return raw.strip().lstrip("@").casefold()


def _session_matches(session_name: str, token: str) -> bool:
    name = (session_name or "").replace("\\", "/").rstrip("/")
    base = name.split("/")[-1]
    if base.lower().endswith(".session"):
        base = base[: -len(".session")]
    base = base.casefold().lstrip("@")
    return bool(base) and (base == token or token in base)


def detach_session_from_jobs(records: list[dict], token: str) -> tuple[list[dict], dict]:
    """Снимает сессию со слушателя, не перенося её каналы на другие аккаунты.

    Каналы этой сессии выпадают из channel_list/assignments, чтобы restore
    не повесил их на соседний аккаунт. Сами строки source_channels не трогаются.
    """
    removed: dict[str, list] = {"sessions": [], "channels": []}
    updated: list[dict] = []
    for record in records:
        item = json.loads(json.dumps(record))
        sessions = [str(x) for x in (item.get("session_name_list") or [])]
        matched = [name for name in sessions if _session_matches(name, token)]
        if not matched:
            updated.append(item)
            continue
        matched_set = set(matched)
        assignments = dict(item.get("assignments") or {})
        drop_refs = {
            ref
            for ref, owner in assignments.items()
            if str(owner) in matched_set or _session_matches(str(owner), token)
        }
        item["session_name_list"] = [name for name in sessions if name not in matched_set]
        item["assignments"] = {
            ref: owner for ref, owner in assignments.items() if ref not in drop_refs
        }
        channel_list = item.get("channel_list") or []
        if isinstance(channel_list, list):
            item["channel_list"] = [
                ref for ref in channel_list if str(ref) not in drop_refs
            ]
        removed["sessions"].extend(matched)
        removed["channels"].extend(sorted(drop_refs))
        if not item["session_name_list"]:
            removed.setdefault("dropped_jobs", []).append(item.get("parser_id"))
            continue
        updated.append(item)
    return updated, removed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="fgsh123")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--jobs", default="")
    parser.add_argument("--sessions-dir", default="")
    parser.add_argument("--accounts-db", default="")
    parser.add_argument("--quarantine-dir", default="")
    return parser


def _jobs_path(raw: str) -> Path:
    if raw:
        return Path(raw)
    return _ROOT / "standalone_discovery" / "data" / "parser_jobs.json"


def _load_jobs(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.get("jobs") or data.get("records") or [])
    return []


def _sqlite_hits(path: Path, token: str) -> list[tuple[str, int]]:
    if not path.is_file():
        return []
    conn = sqlite3.connect(path)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        hits: list[tuple[str, int]] = []
        for table in tables:
            if not _IDENT.fullmatch(table):
                continue
            cols = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            text_cols = [
                col
                for col in cols
                if _IDENT.fullmatch(col)
                and col.lower() in {"username", "session_name", "phone", "name", "login"}
            ]
            if not text_cols:
                continue
            where = " OR ".join(
                f'lower(CAST("{col}" AS TEXT)) LIKE ?' for col in text_cols
            )
            params = [f"%{token}%"] * len(text_cols)
            count = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE {where}', params
            ).fetchone()[0]
            if count:
                hits.append((table, int(count)))
        return hits
    finally:
        conn.close()


def _delete_sqlite_hits(path: Path, token: str) -> None:
    conn = sqlite3.connect(path)
    try:
        for table, _count in _sqlite_hits(path, token):
            cols = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            text_cols = [
                col
                for col in cols
                if _IDENT.fullmatch(col)
                and col.lower() in {"username", "session_name", "phone", "name", "login"}
            ]
            where = " OR ".join(
                f'lower(CAST("{col}" AS TEXT)) LIKE ?' for col in text_cols
            )
            params = [f"%{token}%"] * len(text_cols)
            conn.execute(f'DELETE FROM "{table}" WHERE {where}', params)
        conn.commit()
    finally:
        conn.close()


async def _pg_matches(token: str) -> list[dict]:
    from app_balance.queue.db import acquire, close_pool, init_pool

    await init_pool()
    try:
        async with acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, session_name, telegram_account_id, status, is_enabled
                FROM accounts
                WHERE lower(session_name) LIKE $1
                   OR lower(COALESCE(telegram_account_id, '')) LIKE $1
                ORDER BY id
                """,
                f"%{token}%",
            )
        return [dict(row) for row in rows]
    finally:
        await close_pool()


async def _disable_account(account_id: int) -> None:
    from app_balance.queue.db import acquire, close_pool, init_pool

    await init_pool()
    try:
        async with acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE accounts
                    SET status = 'disabled',
                        is_enabled = false,
                        current_task_id = NULL,
                        updated_at = now()
                    WHERE id = $1
                    """,
                    account_id,
                )
                await conn.execute(
                    """
                    UPDATE task_queue
                    SET status = 'failed',
                        last_error = 'account_removed',
                        finished_at = now(),
                        locked_by = NULL,
                        locked_at = NULL,
                        locked_until = NULL,
                        updated_at = now()
                    WHERE account_id = $1
                      AND status IN ('queued', 'scheduled', 'retry', 'in_progress')
                    """,
                    account_id,
                )
    finally:
        await close_pool()


def _quarantine_session(sessions_dir: Path, session_name: str, quarantine_dir: Path) -> str:
    base = session_name.replace("\\", "/").rstrip("/").split("/")[-1]
    if base.lower().endswith(".session"):
        base = base[: -len(".session")]
    source = sessions_dir / f"{base}.session"
    if not source.is_file():
        return "session_file_not_found"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    target = quarantine_dir / source.name
    if target.exists():
        target = quarantine_dir / f"{base}.already-quarantined.session"
    shutil.move(str(source), str(target))
    return str(target)


def main() -> int:
    import asyncio

    args = _parser().parse_args()
    token = _token(args.username)
    if not token:
        print("пустое имя аккаунта", file=sys.stderr)
        return 2
    matches = asyncio.run(_pg_matches(token))
    print(f"pg_matches={len(matches)}")
    for row in matches:
        print(
            f"id={row['id']} session_name={row['session_name']} "
            f"status={row['status']} enabled={row['is_enabled']}"
        )
    jobs_path = _jobs_path(args.jobs)
    jobs = _load_jobs(jobs_path)
    _updated, removed = detach_session_from_jobs(jobs, token)
    print(
        f"jobs_sessions={removed['sessions']} jobs_channels={len(removed['channels'])}"
    )
    accounts_db = Path(args.accounts_db) if args.accounts_db else None
    sqlite_hits: list[tuple[str, int]] = []
    if accounts_db is not None:
        sqlite_hits = _sqlite_hits(accounts_db, token)
        print(f"sqlite_hits={sqlite_hits}")
    if not args.execute:
        print("dry-run: изменений нет")
        return 0
    if len(matches) != 1:
        print("отказ: нужна ровно одна строка accounts", file=sys.stderr)
        return 3
    account = matches[0]
    backup = {
        "account_id": account["id"],
        "session_name": account["session_name"],
        "jobs_removed": removed,
    }
    backup_path = jobs_path.parent / f"quarantine-{token}.json"
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
    if jobs_path.is_file():
        jobs_path.write_text(
            json.dumps(_updated, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    asyncio.run(_disable_account(int(account["id"])))
    if accounts_db is not None and sqlite_hits:
        if sum(count for _table, count in sqlite_hits) != 1:
            print("sqlite: совпадений не одно, строки не удалены", file=sys.stderr)
        else:
            _delete_sqlite_hits(accounts_db, token)
    if args.sessions_dir:
        dest = _quarantine_session(
            Path(args.sessions_dir),
            str(account["session_name"]),
            Path(args.quarantine_dir or (Path(args.sessions_dir) / "_quarantine")),
        )
        print(f"session={dest}")
    print(f"disabled id={account['id']} backup={backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
