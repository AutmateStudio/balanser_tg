"""Разовый замер: enqueue_parser_remove_channels на 4000 каналов (реальная PG).

Не часть pytest suite — ad-hoc проверка, что batch-путь укладывается в
таймаут прокси (цель — заметно меньше 5 минут). Запуск:

    docker compose run --rm test python scripts/perf_check_remove_channels_4000.py

Создаёт временный тестовый аккаунт + 4000 dedup-задач с уникальным parser_id/
suffix, затем удаляет всё за собой (accounts, task_queue).
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "standalone_discovery"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

N_CHANNELS = 4000
PREFIX = "perfcheck_rm_"


async def main() -> None:
    from app_balance.queue import db

    await db.init_pool()

    suffix = uuid.uuid4().hex[:10]
    parser_id = f"{PREFIX}parser_{suffix}"

    async with db.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO accounts (session_name, status, is_enabled) "
            "VALUES ($1, 'active', true) RETURNING id",
            f"{PREFIX}session_{suffix}",
        )

    channel_refs = [f"@{PREFIX}ch_{suffix}_{i}" for i in range(N_CHANNELS)]
    clump = MagicMock()
    clump.assignments = {ref: f"{PREFIX}session_{suffix}" for ref in channel_refs}

    from discovery_api.queue.producer import enqueue_parser_remove_channels

    try:
        with patch(
            "discovery_api.session_registry.get_clump",
            return_value=clump,
        ):
            t0 = time.perf_counter()
            result = await enqueue_parser_remove_channels(
                parser_id=parser_id,
                channel_list=channel_refs,
                action_id=f"{PREFIX}action_{suffix}",
            )
            elapsed = time.perf_counter() - t0

        print(f"channels_requested={N_CHANNELS}")
        print(f"tasks_created={len(result.task_ids)}")
        print(f"elapsed_seconds={elapsed:.2f}")
    finally:
        async with db.acquire() as conn:
            await conn.execute(
                "DELETE FROM task_queue WHERE dedup_key LIKE $1",
                f"parser_remove_channel:{parser_id}:%",
            )
            await conn.execute("DELETE FROM accounts WHERE id = $1", account_id)


if __name__ == "__main__":
    asyncio.run(main())
