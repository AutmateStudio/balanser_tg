"""B6/C5 — unit-тесты pick_and_reserve (exclude_account_ids, SQL-контракт)."""
from __future__ import annotations

from app_balance.queue.accounts import _PICK_FOR_UPDATE_EXCLUDE_SQL, _PICK_FOR_UPDATE_SQL


def test_pick_for_update_sql_has_skip_locked() -> None:
    sql = _PICK_FOR_UPDATE_SQL.lower()
    assert "for update skip locked" in sql
    assert "current_task_id is null" in sql


def test_pick_exclude_sql_filters_ids() -> None:
    sql = _PICK_FOR_UPDATE_EXCLUDE_SQL.lower()
    assert "not (id = any($1::bigint[]))" in sql
    assert "for update skip locked" in sql
    assert "order by last_used_at" in sql
