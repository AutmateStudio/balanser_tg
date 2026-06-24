"""Общие фикстуры pytest."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app_balance.queue import db

_sd_root = Path(__file__).resolve().parents[1] / "standalone_discovery"
if _sd_root.is_dir() and str(_sd_root) not in sys.path:
    sys.path.insert(0, str(_sd_root))

# Приоритет тестовых задач выше продовых (500) — изоляция от фонового queue-worker.
TEST_ISOLATION_PRIORITY = int(os.getenv("PYTEST_TEST_PRIORITY", "2000000000"))


def _has_dsn() -> bool:
    return bool(os.getenv("QUEUE_DATABASE_URL", "").strip())


requires_pg = pytest.mark.skipif(
    not _has_dsn(),
    reason="QUEUE_DATABASE_URL не задан — интеграционные тесты пропущены",
)


@pytest.fixture
async def pg_pool():
    """Инициализированный пул; закрывается после теста."""
    await db.close_pool()
    await db.init_pool()
    yield
    await db.close_pool()