"""Общий cleanup PG для integration-тестов (D5: account_resource_usage → task_queue FK)."""
from __future__ import annotations

from app_balance.queue import db


async def cleanup_queue_test_data(
    *,
    dedup_key_like: str | None = None,
    session_name_like: str | None = None,
    session_name_eq: str | None = None,
) -> None:
    """Удаляет тестовые строки; usage удаляется до task_queue и accounts."""
    async with db.acquire() as conn:
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
                DELETE FROM account_resource_usage
                WHERE account_id IN (
                    SELECT id FROM accounts WHERE session_name LIKE $1
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

        if dedup_key_like and session_name_like:
            await conn.execute(
                """
                UPDATE accounts
                SET current_task_id = NULL
                WHERE session_name LIKE $1
                   OR current_task_id IN (
                       SELECT id FROM task_queue WHERE dedup_key LIKE $2
                   )
                """,
                session_name_like,
                dedup_key_like,
            )
        elif session_name_like:
            await conn.execute(
                "UPDATE accounts SET current_task_id = NULL WHERE session_name LIKE $1",
                session_name_like,
            )
        elif dedup_key_like:
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
                """
                UPDATE source_channels
                SET assigned_account_id = NULL
                WHERE assigned_account_id IN (
                    SELECT id FROM accounts WHERE session_name LIKE $1
                )
                """,
                session_name_like,
            )
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
                UPDATE source_channels
                SET assigned_account_id = NULL
                WHERE assigned_account_id IN (
                    SELECT id FROM accounts WHERE session_name = $1
                )
                """,
                session_name_eq,
            )
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
            await conn.execute(
                "UPDATE accounts SET current_task_id = NULL WHERE session_name = $1",
                session_name_eq,
            )

        if dedup_key_like:
            await conn.execute(
                "DELETE FROM task_queue WHERE dedup_key LIKE $1",
                dedup_key_like,
            )
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
