"""B9 — unit-тесты SQL TaskAttemptsRepo."""
from __future__ import annotations

from app_balance.queue.task_attempts import _FINISH_SQL, _INSERT_SQL


def test_insert_sql_uses_running_status() -> None:
    assert "'running'" in _INSERT_SQL


def test_finish_sql_only_updates_running() -> None:
    sql = _FINISH_SQL.lower()
    assert "status = 'running'" in sql
    assert "finished_at is null" in sql
