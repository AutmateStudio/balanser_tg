#!/usr/bin/env python3
"""A10 — CLI sync accounts: SQLite + SESSIONS_DIR + clump → PG accounts.

Рекомендуемый порядок на dev/staging:
  make migrate-queue
  python scripts/sync_accounts_to_pg.py
  docker compose up -d queue-worker

Env:
  QUEUE_DATABASE_URL  — обязательно
  ACCOUNT_STORE_PATH    — SQLite telegram_accounts (default: standalone_discovery/.../telegram_accounts.db)
  SESSIONS_DIR          — каталог *.session (default: /app/sessions)
  PARSER_STORE_PATH     — parser_jobs.json для membership clump (default: .../parser_jobs.json)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app_balance.queue import db
from app_balance.queue.accounts_sync import sync_accounts_to_pg, sync_config_from_env


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Telegram accounts из discovery (SQLite + disk + clump) в PG accounts"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать план изменений без записи в PG",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    config = sync_config_from_env()
    try:
        result = await sync_accounts_to_pg(config, dry_run=args.dry_run)
    except RuntimeError as exc:
        logging.error("%s", exc)
        return 1
    finally:
        await db.close_pool()

    print(
        f"sync завершён: total={result.total} "
        f"inserted={result.inserted} updated={result.updated} "
        f"unchanged={result.unchanged}"
        + (" (dry-run)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
