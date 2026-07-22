"""PG Queue migration runner (Windows-friendly, asyncpg — без psql/Docker).

SSH tunnel (отдельный терминал):
    ssh -L 15432:127.0.0.1:5432 ubuntu@YOUR_VPS

Run:
    set QUEUE_DATABASE_URL=postgresql://lead_monitor_owner:PASS@127.0.0.1:15432/lead_monitor?sslmode=disable
    python scripts/migrate_queue.py --dry-run
    python scripts/migrate_queue.py
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
DB_DIR = ROOT / "DB"


def log(msg: str) -> None:
    print(f"[migrate-queue] {msg}")


def die(msg: str) -> None:
    print(f"[migrate-queue][ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS public._migrations_applied (
  name varchar(255) PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
""".strip()


async def ensure_ledger(conn: asyncpg.Connection) -> None:
    await conn.execute(LEDGER_SQL)


async def is_applied(conn: asyncpg.Connection, name: str) -> bool:
    row = await conn.fetchval(
        "SELECT 1 FROM public._migrations_applied WHERE name = $1 LIMIT 1",
        name,
    )
    return row == 1


async def resolve_mode(conn: asyncpg.Connection, mode: str) -> str:
    if mode != "auto":
        log(f"Mode (manual): {mode}")
        return mode
    has_main = await conn.fetchval(
        "SELECT (to_regclass('public.source_channels') IS NOT NULL "
        "AND to_regclass('public.platforms') IS NOT NULL);"
    )
    resolved = "integrate" if has_main else "greenfield"
    log(f"Mode (auto): {resolved}")
    return resolved


async def apply_once(conn: asyncpg.Connection, path: Path, *, dry_run: bool) -> None:
    base = path.name
    if await is_applied(conn, base):
        log(f"skip (already applied): {base}")
        return
    if dry_run:
        log(f"DRY-RUN would apply: {base}")
        return
    log(f"apply: {base}")
    sql = path.read_text(encoding="utf-8")
    async with conn.transaction():
        await conn.execute(sql)
        await conn.execute(
            "INSERT INTO public._migrations_applied(name) VALUES ($1) "
            "ON CONFLICT (name) DO NOTHING",
            base,
        )
    log(f"OK: {base}")


async def apply_seed(conn: asyncpg.Connection, path: Path, *, dry_run: bool) -> None:
    base = path.name
    if dry_run:
        log(f"DRY-RUN would apply seed: {base}")
        return
    log(f"seed: {base}")
    sql = path.read_text(encoding="utf-8")
    async with conn.transaction():
        await conn.execute(sql)
        await conn.execute(
            "INSERT INTO public._migrations_applied(name) VALUES ($1) "
            "ON CONFLICT (name) DO UPDATE SET applied_at = now()",
            base,
        )
    log(f"OK: {base}")


async def run(args: argparse.Namespace) -> None:
    dsn = args.dsn.strip()
    if not dsn:
        die("Set QUEUE_DATABASE_URL or --dsn")

    try:
        conn = await asyncpg.connect(dsn, timeout=10)
    except Exception as exc:
        die(f"Cannot connect: {exc}")

    try:
        log("Connected.")
        await ensure_ledger(conn)
        mode = await resolve_mode(conn, args.mode)
        schema = DB_DIR / ("A8_integrate_main_db.sql" if mode == "integrate" else "BD_schema.sql")
        seed = DB_DIR / "A9_seed.sql"
        for path in (schema, seed):
            if not path.is_file():
                die(f"File not found: {path}")

        for name in (
            schema.name,
            "A10_attempt_status_running.sql",
            "A11_g6_error_detector.sql",
            "A12_g7_monitoring_views.sql",
        ):
            path = schema if name == schema.name else DB_DIR / name
            if not path.is_file():
                die(f"File not found: {path}")
            await apply_once(conn, path, dry_run=args.dry_run)

        if not args.no_seed:
            await apply_seed(conn, seed, dry_run=args.dry_run)
        else:
            log("seed skipped (--no-seed)")

        log("Done. Applied migrations:")
        rows = await conn.fetch(
            "SELECT name, applied_at FROM public._migrations_applied ORDER BY applied_at"
        )
        for row in rows:
            print(f"  {row['name']}\t{row['applied_at']}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply PG queue migrations (asyncpg)")
    parser.add_argument("--dsn", default=os.getenv("QUEUE_DATABASE_URL", ""))
    parser.add_argument("--mode", default="auto", choices=("auto", "integrate", "greenfield"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-seed", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
