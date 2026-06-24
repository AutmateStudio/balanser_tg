"""B6 — pick / reserve / release аккаунтов (ТЗ §16; план B6).

Схема: DB/BD_schema.sql § accounts + индекс idx_accounts_pick_available
(status='active' AND is_enabled AND current_task_id IS NULL).
Резерв атомарен: UPDATE проходит только если аккаунт ещё свободен.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app_balance.queue.db import acquire, transaction


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    session_name: str
    status: str
    is_enabled: bool
    current_task_id: int | None
    cooldown_until: datetime | None
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class DualReserveResult:
    """C4: зарезервированная пара аккаунтов для move_channel."""

    source: Account
    target: Account


_PICK_SQL = """
SELECT id, session_name, status, is_enabled, current_task_id, cooldown_until, last_used_at
FROM accounts
WHERE status IN ('active', 'cooldown')
  AND is_enabled = true
  AND current_task_id IS NULL
  AND (cooldown_until IS NULL OR cooldown_until <= now())
ORDER BY last_used_at ASC NULLS FIRST, id ASC
LIMIT 1
"""

# Тот же отбор, но с блокировкой строки для атомарного pick+reserve между воркерами.
_PICK_FOR_UPDATE_SQL = _PICK_SQL.rstrip() + "\nFOR UPDATE SKIP LOCKED\n"

_PICK_FOR_UPDATE_EXCLUDE_SQL = """
SELECT id, session_name, status, is_enabled, current_task_id, cooldown_until, last_used_at
FROM accounts
WHERE status IN ('active', 'cooldown')
  AND is_enabled = true
  AND current_task_id IS NULL
  AND (cooldown_until IS NULL OR cooldown_until <= now())
  AND NOT (id = ANY($1::bigint[]))
ORDER BY last_used_at ASC NULLS FIRST, id ASC
LIMIT 1
FOR UPDATE SKIP LOCKED
"""

_RESERVE_SQL = """
UPDATE accounts
SET current_task_id = $2, last_used_at = now()
WHERE id = $1 AND current_task_id IS NULL
RETURNING id
"""

_RELEASE_SQL = """
UPDATE accounts
SET current_task_id = NULL
WHERE id = $1
RETURNING id
"""

_SET_COOLDOWN_SQL = """
UPDATE accounts
SET cooldown_until = GREATEST(COALESCE(cooldown_until, $2::timestamptz), $2::timestamptz),
    status = CASE
        WHEN status = 'banned' THEN status
        ELSE 'cooldown'
    END,
    updated_at = now()
WHERE session_name = $1
RETURNING id
"""

_SET_BANNED_SQL = """
UPDATE accounts
SET status = 'banned',
    cooldown_until = NULL,
    last_error = $2,
    last_error_at = now(),
    updated_at = now()
WHERE session_name = $1
RETURNING id
"""

# C4: блокировка пары аккаунтов перед атомарным dual reserve.
_PAIR_LOCK_SQL = """
SELECT id, session_name, status, is_enabled, current_task_id, cooldown_until, last_used_at
FROM accounts
WHERE id IN ($1, $2)
  AND status IN ('active', 'cooldown')
  AND is_enabled = true
  AND current_task_id IS NULL
  AND (cooldown_until IS NULL OR cooldown_until <= now())
FOR UPDATE
"""


def _row_to_account(row) -> Account:
    return Account(
        id=row["id"],
        session_name=row["session_name"],
        status=row["status"],
        is_enabled=row["is_enabled"],
        current_task_id=row["current_task_id"],
        cooldown_until=row["cooldown_until"],
        last_used_at=row["last_used_at"],
    )


class AccountsRepo:
    """Выбор и резервирование аккаунтов под задачу (B6)."""

    async def pick(self) -> Account | None:
        async with acquire() as conn:
            row = await conn.fetchrow(_PICK_SQL)
            return _row_to_account(row) if row is not None else None

    async def reserve(self, account_id: int, task_id: int) -> bool:
        """Атомарно занимает аккаунт под задачу. False — уже занят."""
        async with acquire() as conn:
            reserved = await conn.fetchval(_RESERVE_SQL, account_id, task_id)
            return reserved is not None

    async def reserve_pair(
        self, source_id: int, target_id: int, task_id: int
    ) -> DualReserveResult | None:
        """C4: атомарно резервирует source + target в одной транзакции.

        None — аккаунт недоступен, занят или пара не прошла валидацию.
        """
        if source_id == target_id:
            return None

        async with transaction() as conn:
            rows = await conn.fetch(_PAIR_LOCK_SQL, source_id, target_id)
            if len(rows) != 2:
                return None

            by_id = {row["id"]: _row_to_account(row) for row in rows}
            source = by_id.get(source_id)
            target = by_id.get(target_id)
            if source is None or target is None:
                return None

            reserved_source = await conn.fetchval(
                _RESERVE_SQL, source_id, task_id
            )
            if reserved_source is None:
                return None

            reserved_target = await conn.fetchval(
                _RESERVE_SQL, target_id, task_id
            )
            if reserved_target is None:
                return None

            source_row = await conn.fetchrow(
                """
                SELECT id, session_name, status, is_enabled, current_task_id,
                       cooldown_until, last_used_at
                FROM accounts WHERE id = $1
                """,
                source_id,
            )
            target_row = await conn.fetchrow(
                """
                SELECT id, session_name, status, is_enabled, current_task_id,
                       cooldown_until, last_used_at
                FROM accounts WHERE id = $1
                """,
                target_id,
            )
            if source_row is None or target_row is None:
                return None

            return DualReserveResult(
                source=_row_to_account(source_row),
                target=_row_to_account(target_row),
            )

    async def release(self, account_id: int) -> None:
        async with acquire() as conn:
            await conn.execute(_RELEASE_SQL, account_id)

    async def get_by_id(self, account_id: int) -> Account | None:
        async with acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, session_name, status, is_enabled, current_task_id,
                       cooldown_until, last_used_at
                FROM accounts
                WHERE id = $1
                """,
                account_id,
            )
            return _row_to_account(row) if row is not None else None

    async def set_cooldown(self, session_name: str, until: datetime) -> bool:
        """Flood/cooldown: продлевает cooldown_until, status → cooldown (кроме banned)."""
        name = (session_name or "").strip()
        if not name:
            return False
        async with acquire() as conn:
            row = await conn.fetchrow(_SET_COOLDOWN_SQL, name, until)
            return row is not None

    async def set_banned(self, session_name: str, *, reason: str | None = None) -> bool:
        """Telegram ban: status → banned, сбрасывает cooldown."""
        name = (session_name or "").strip()
        if not name:
            return False
        async with acquire() as conn:
            row = await conn.fetchrow(_SET_BANNED_SQL, name, reason)
            return row is not None

    async def pick_and_reserve(
        self,
        task_id: int,
        *,
        exclude_account_ids: frozenset[int] | None = None,
    ) -> Account | None:
        """Атомарный pick+reserve в одной транзакции (FOR UPDATE SKIP LOCKED).

        exclude_account_ids — аккаунты, уже отвергнутые по resource check в текущем
        dispatch; не возвращаются повторно (C5: иначе бесконечный цикл auto-pick).

        Два воркера не получат один аккаунт.
        """
        excluded = list(exclude_account_ids or ())
        async with transaction() as conn:
            if excluded:
                row = await conn.fetchrow(
                    _PICK_FOR_UPDATE_EXCLUDE_SQL, excluded
                )
            else:
                row = await conn.fetchrow(_PICK_FOR_UPDATE_SQL)
            if row is None:
                return None
            await conn.execute(
                "UPDATE accounts SET current_task_id = $2, last_used_at = now(), "
                "status = 'active' "
                "WHERE id = $1",
                row["id"],
                task_id,
            )
            return _row_to_account(row)
