"""E7 — сверка ops_catalog с seed и (опционально) с БД.

По умолчанию проверяет только seed (детерминированно, без PG) — пригодно для CI.
Сверка с живой БД выполняется только при явном флаге --db.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from app_balance.queue.ops_catalog_verify import (
    verify_db_against_catalog,
    verify_seed_against_catalog,
)


def _print_result(label: str, issues: list[str]) -> bool:
    if not issues:
        print(f"[ok] {label}")
        return True
    print(f"[fail] {label}")
    for issue in issues:
        print(f"- {issue}")
    return False


async def _run_db_check() -> list[str]:
    from app_balance.queue import db

    await db.init_pool()
    try:
        return await verify_db_against_catalog()
    finally:
        await db.close_pool()


def main() -> int:
    parser = argparse.ArgumentParser(description="Сверка ops_catalog и seed/БД.")
    parser.add_argument(
        "--db",
        action="store_true",
        help="Дополнительно сверить живую БД (требует QUEUE_DATABASE_URL).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Машиночитаемый вывод результата.",
    )
    args = parser.parse_args()

    results: dict[str, list[str]] = {"seed": verify_seed_against_catalog()}

    if args.db:
        if not os.getenv("QUEUE_DATABASE_URL", "").strip():
            results["db"] = ["QUEUE_DATABASE_URL не задан"]
        else:
            results["db"] = asyncio.run(_run_db_check())

    ok = not any(results[label] for label in results)

    if args.json:
        print(json.dumps({"ok": ok, "results": results}, ensure_ascii=False, indent=2))
    else:
        for label, issues in results.items():
            _print_result(label, issues)
        if not args.db:
            print("[skip] db: запустите с --db для сверки с PostgreSQL")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
