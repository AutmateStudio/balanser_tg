"""G4-доп. — тесты self-healing reaper зависших 'idle in transaction' сессий.

Инцидент 2026-08-11: при пересоздании контейнера in-process worker'ы могут
обрывать соединение к PG посреди открытой транзакции, оставляя сессию
'idle in transaction' навсегда (до TCP keepalive) — держит блокировки строк
accounts/task_queue. См. docs/discovery-vps104.local.md (lidogen_site).
"""
from __future__ import annotations

import asyncio

import pytest

from app_balance.queue.monitoring.idle_transaction_reaper import (
    IdleTransactionReaperConfig,
    reap_idle_transactions,
)
from tests.conftest import requires_pg


def test_config_defaults() -> None:
    cfg = IdleTransactionReaperConfig()
    assert cfg.enabled is True
    assert cfg.max_age_seconds == 300


def test_config_from_env_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IDLE_TRANSACTION_REAPER_ENABLED", "false")
    monkeypatch.setenv("IDLE_TRANSACTION_REAPER_MAX_AGE_SECONDS", "60")
    cfg = IdleTransactionReaperConfig.from_env()
    assert cfg.enabled is False
    assert cfg.max_age_seconds == 60


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_reap_ignores_fresh_idle_transaction() -> None:
    """Свежая (только что открытая) idle-in-transaction сессия НЕ трогается —

    порог по возрасту защищает от убийства легитимной активной работы."""
    import asyncpg

    from app_balance.queue.db import _get_dsn

    stuck = await asyncpg.connect(_get_dsn())
    try:
        tx = stuck.transaction()
        await tx.start()
        await stuck.execute("SELECT 1")

        reaped = await reap_idle_transactions(max_age_seconds=300)

        stuck_pid = stuck.get_server_pid()
        assert stuck_pid not in reaped
    finally:
        try:
            await stuck.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        await stuck.close()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_reap_terminates_old_idle_transaction() -> None:
    """Зависшая транзакция (BEGIN без COMMIT), возраст >= порога (0) — reaper

    её находит и завершает через pg_terminate_backend, снимая блокировку."""
    import asyncpg

    from app_balance.queue.db import _get_dsn

    stuck = await asyncpg.connect(_get_dsn())
    stuck_pid = stuck.get_server_pid()
    try:
        tx = stuck.transaction()
        await tx.start()
        await stuck.execute("SELECT 1")
        await asyncio.sleep(0.05)

        reaped = await reap_idle_transactions(max_age_seconds=0)

        assert stuck_pid in reaped
    finally:
        try:
            await stuck.close(timeout=2)
        except Exception:  # noqa: BLE001
            pass
