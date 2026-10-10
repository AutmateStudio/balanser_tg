#!/usr/bin/env python3
"""Разовый выход из неотслеживаемых каналов через очередь.

По умолчанию dry-run: CSV в stdout, очередь не меняется.
--execute ставит parser_remove_channel
(created_by=leave_untracked_channels_oneoff), чтобы сработали RPH и cooldown.

В список попадают только source_channels без active-проекта.
Аккаунты из LEAVE_CHANNEL_DENY_ACCOUNTS пропускаются.
Диалоги Telethon не сканируются: .session заняты discovery-api, а выход
из чата, которого нет в source_channels, нельзя обосновать как снятие
с отслеживания.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SD = _ROOT / "standalone_discovery"
if _SD.is_dir() and str(_SD) not in sys.path:
    sys.path.insert(0, str(_SD))

from app_balance.queue.db import close_pool, init_pool, acquire  # noqa: E402
from app_balance.queue.producers.inactive_channel_remove import (  # noqa: E402
    account_is_denied,
)
from app_balance.queue.source_channels import (  # noqa: E402
    _LIST_INACTIVE_ON_SESSIONS_SQL,
)
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo  # noqa: E402


CREATED_BY = "leave_untracked_channels_oneoff"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="поставить задачи в очередь (без флага только CSV)",
    )
    parser.add_argument(
        "--stale-seconds",
        type=int,
        default=0,
        help="минимальный возраст снятия с проекта, секунды (0 = все текущие)",
    )
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument(
        "--csv",
        default="",
        help="куда записать CSV; пусто — stdout",
    )
    return parser


async def _load_rows(stale_seconds: int, limit: int) -> list[dict]:
    await init_pool()
    async with acquire() as conn:
        rows = await conn.fetch(_LIST_INACTIVE_ON_SESSIONS_SQL, stale_seconds, limit)
    return [dict(row) for row in rows]


def _ref(row: dict) -> str:
    url = (row.get("external_url") or "").strip()
    if url:
        return url
    return (row.get("external_channel_id") or "").strip()


async def _enqueue(rows: list[dict]) -> tuple[int, int]:
    repo = TaskQueueRepo()
    created = 0
    skipped = 0
    for row in rows:
        ref = _ref(row)
        parser_id = "unassigned"
        from app_balance.queue.producers.inactive_channel_remove import _dedup_key

        result = await repo.enqueue(
            EnqueueInput(
                task_type_code="parser_remove_channel",
                channel_id=int(row["id"]),
                account_id=int(row["assigned_account_id"]),
                dedup_key=_dedup_key(parser_id, ref),
                created_by=CREATED_BY,
                payload={
                    "parser_id": parser_id,
                    "channel_ref": ref,
                    "session_name": row["session_name"],
                    "leave_without_clump": True,
                },
            )
        )
        if result.created:
            created += 1
        else:
            skipped += 1
    return created, skipped


async def _main() -> int:
    args = _parser().parse_args()
    try:
        rows = await _load_rows(args.stale_seconds, args.limit)
    finally:
        await close_pool()
    eligible = [
        row
        for row in rows
        if not account_is_denied(str(row.get("session_name") or ""))
    ]
    denied = len(rows) - len(eligible)
    out = open(args.csv, "w", newline="", encoding="utf-8") if args.csv else sys.stdout
    try:
        writer = csv.writer(out)
        writer.writerow(
            ["account_id", "session_name", "channel_id", "ref", "reason"]
        )
        for row in eligible:
            writer.writerow(
                [
                    row["assigned_account_id"],
                    row["session_name"],
                    row["id"],
                    _ref(row),
                    "no_active_project",
                ]
            )
    finally:
        if args.csv:
            out.close()
    print(
        f"кандидатов={len(eligible)} скрыто_deny={denied} execute={args.execute}",
        file=sys.stderr,
    )
    if not args.execute:
        return 0
    await init_pool()
    try:
        created, skipped = await _enqueue(eligible)
    finally:
        await close_pool()
    print(f"поставлено={created} уже_были={skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
