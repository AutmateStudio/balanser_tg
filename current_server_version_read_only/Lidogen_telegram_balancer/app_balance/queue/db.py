import os

import asyncpg
import dotenv
from contextlib import asynccontextmanager
from typing import AsyncGenerator

dotenv.load_dotenv()

_pool: asyncpg.Pool | None = None


def _get_dsn() -> str:
    dsn = os.getenv("QUEUE_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("QUEUE_DATABASE_URL не задан")
    return dsn


async def init_pool(dsn: str | None = None) -> None:
    global _pool
    if _pool is not None:
        return
    _pool = await asyncpg.create_pool(
        dsn or _get_dsn(),
        min_size=1,
        max_size=20,
        timeout=20,
    )


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Пул не инициализирован — вызовите init_pool()")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except RuntimeError:
            _pool.terminate()
        _pool = None


@asynccontextmanager
async def acquire() -> AsyncGenerator[asyncpg.Connection, None]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction() -> AsyncGenerator[asyncpg.Connection, None]:
    async with acquire() as conn:
        async with conn.transaction():
            yield conn


async def healthcheck() -> bool:
    async with acquire() as conn:
        return await conn.fetchval("SELECT 1") == 1


class _RollbackProbeError(RuntimeError):
    """Маркер для smoke-теста rollback в transaction()."""


async def verify_transaction_rollback() -> None:
    """B1: commit сохраняет строку, исключение в transaction() откатывает INSERT.

    Требует живой PG (QUEUE_DATABASE_URL). Temp-таблица привязана к сессии —
    пул на время проверки создаётся с max_size=1, чтобы transaction() и acquire()
    шли через одно соединение.
    """
    await close_pool()
    global _pool
    _pool = await asyncpg.create_pool(_get_dsn(), min_size=1, max_size=1, timeout=20)

    try:
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS _b1_tx_probe (
                    id int PRIMARY KEY
                ) ON COMMIT PRESERVE ROWS
                """
            )
            await conn.execute("DELETE FROM _b1_tx_probe")

        async with transaction() as conn:
            await conn.execute("INSERT INTO _b1_tx_probe (id) VALUES (1)")

        async with acquire() as conn:
            committed = await conn.fetchval("SELECT COUNT(*) FROM _b1_tx_probe")
            if committed != 1:
                raise AssertionError(
                    f"commit: ожидалась 1 строка после INSERT, получено {committed}"
                )

        try:
            async with transaction() as conn:
                await conn.execute("INSERT INTO _b1_tx_probe (id) VALUES (2)")
                raise _RollbackProbeError()
        except _RollbackProbeError:
            pass

        async with acquire() as conn:
            after_rollback = await conn.fetchval("SELECT COUNT(*) FROM _b1_tx_probe")
            if after_rollback != 1:
                raise AssertionError(
                    f"rollback: ожидалась 1 строка после отката, получено {after_rollback}"
                )
    finally:
        await close_pool()

