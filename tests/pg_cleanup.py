"""Общий cleanup PG для integration-тестов.

Строгий порядок удаления (FK-safe), одна транзакция:
  1. accounts.current_task_id → NULL (FK accounts → task_queue)
  2. source_channels.assigned_account_id → NULL (FK source_channels → accounts)
  3. account_resource_usage   (FK → task_queue, accounts, task_attempts)
  4. task_attempts            (FK → task_queue, accounts)
  5. task_queue.*_account_id → NULL, затем DELETE task_queue
  6. accounts

Очистка идёт и по dedup_key (тестовые задачи), и по session_name (тестовые
аккаунты). На shared PG обязательна остановка queue-worker (см. guard в
tests/conftest.py), иначе worker параллельно пересоздаёт строки и возникает
FK-гонка даже при корректном порядке.
"""
from __future__ import annotations

from app_balance.queue import db


async def cleanup_queue_test_data(
    *,
    dedup_key_like: str | None = None,
    session_name_like: str | None = None,
    session_name_eq: str | None = None,
) -> None:
    if not any((dedup_key_like, session_name_like, session_name_eq)):
        return

    async with db.acquire() as conn:
        async with conn.transaction():
            # --- 1. accounts.current_task_id → NULL --------------------------
            if dedup_key_like:
                await conn.execute(
                    """
                    UPDATE accounts
                    SET current_task_id = NULL
                    WHERE current_task_id IN (
                        SELECT id FROM task_queue WHERE dedup_key LIKE $1
                    )
                    """,
                    dedup_key_like,
                )
            if session_name_like:
                await conn.execute(
                    "UPDATE accounts SET current_task_id = NULL "
                    "WHERE session_name LIKE $1",
                    session_name_like,
                )
            if session_name_eq:
                await conn.execute(
                    "UPDATE accounts SET current_task_id = NULL "
                    "WHERE session_name = $1",
                    session_name_eq,
                )

            # --- 2. source_channels.assigned_account_id → NULL ---------------
            if session_name_like:
                await conn.execute(
                    """
                    UPDATE source_channels
                    SET assigned_account_id = NULL
                    WHERE assigned_account_id IN (
                        SELECT id FROM accounts WHERE session_name LIKE $1
                    )
                    """,
                    session_name_like,
                )
            if session_name_eq:
                await conn.execute(
                    """
                    UPDATE source_channels
                    SET assigned_account_id = NULL
                    WHERE assigned_account_id IN (
                        SELECT id FROM accounts WHERE session_name = $1
                    )
                    """,
                    session_name_eq,
                )

            # --- 3. account_resource_usage -----------------------------------
            if dedup_key_like:
                await conn.execute(
                    """
                    DELETE FROM account_resource_usage
                    WHERE task_id IN (
                        SELECT id FROM task_queue WHERE dedup_key LIKE $1
                    )
                    """,
                    dedup_key_like,
                )
            if session_name_like:
                await conn.execute(
                    """
                    DELETE FROM account_resource_usage
                    WHERE account_id IN (
                        SELECT id FROM accounts WHERE session_name LIKE $1
                    )
                       OR task_id IN (
                        SELECT id FROM task_queue WHERE account_id IN (
                            SELECT id FROM accounts WHERE session_name LIKE $1
                        )
                    )
                    """,
                    session_name_like,
                )
            if session_name_eq:
                await conn.execute(
                    """
                    DELETE FROM account_resource_usage
                    WHERE account_id IN (
                        SELECT id FROM accounts WHERE session_name = $1
                    )
                    """,
                    session_name_eq,
                )

            # --- 4. task_attempts --------------------------------------------
            if dedup_key_like:
                await conn.execute(
                    """
                    DELETE FROM task_attempts
                    WHERE task_id IN (
                        SELECT id FROM task_queue WHERE dedup_key LIKE $1
                    )
                    """,
                    dedup_key_like,
                )
            if session_name_like:
                await conn.execute(
                    """
                    DELETE FROM task_attempts
                    WHERE account_id IN (
                        SELECT id FROM accounts WHERE session_name LIKE $1
                    )
                       OR task_id IN (
                        SELECT id FROM task_queue WHERE account_id IN (
                            SELECT id FROM accounts WHERE session_name LIKE $1
                        )
                    )
                    """,
                    session_name_like,
                )
            if session_name_eq:
                await conn.execute(
                    """
                    DELETE FROM task_attempts
                    WHERE account_id IN (
                        SELECT id FROM accounts WHERE session_name = $1
                    )
                    """,
                    session_name_eq,
                )

            # --- 5. task_queue: снять ссылки на accounts, затем удалить -------
            if session_name_like:
                await conn.execute(
                    """
                    UPDATE task_queue
                    SET account_id = NULL,
                        source_account_id = NULL,
                        target_account_id = NULL
                    WHERE account_id IN (
                        SELECT id FROM accounts WHERE session_name LIKE $1
                    )
                       OR source_account_id IN (
                        SELECT id FROM accounts WHERE session_name LIKE $1
                    )
                       OR target_account_id IN (
                        SELECT id FROM accounts WHERE session_name LIKE $1
                    )
                    """,
                    session_name_like,
                )
            if session_name_eq:
                await conn.execute(
                    """
                    UPDATE task_queue
                    SET account_id = NULL,
                        source_account_id = NULL,
                        target_account_id = NULL
                    WHERE account_id IN (SELECT id FROM accounts WHERE session_name = $1)
                       OR source_account_id IN (SELECT id FROM accounts WHERE session_name = $1)
                       OR target_account_id IN (SELECT id FROM accounts WHERE session_name = $1)
                    """,
                    session_name_eq,
                )
            if dedup_key_like:
                await conn.execute(
                    "DELETE FROM task_queue WHERE dedup_key LIKE $1",
                    dedup_key_like,
                )

            # --- 6. accounts -------------------------------------------------
            if session_name_like:
                await conn.execute(
                    "DELETE FROM accounts WHERE session_name LIKE $1",
                    session_name_like,
                )
            if session_name_eq:
                await conn.execute(
                    "DELETE FROM accounts WHERE session_name = $1",
                    session_name_eq,
                )
