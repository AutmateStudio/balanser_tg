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
                DELETE FROM account_resource_usage aru
                USING task_queue tq
                WHERE aru.task_id = tq.id AND tq.dedup_key LIKE $1
                """,
                dedup_key_like,
            )
            await conn.execute(
                """
                DELETE FROM task_attempts ta
                USING task_queue tq
                WHERE ta.task_id = tq.id AND tq.dedup_key LIKE $1
                """,
                dedup_key_like,
            )
        if session_name_like:
            await conn.execute(
                """
                DELETE FROM account_resource_usage
                WHERE task_id IN (
                    SELECT t.id FROM task_queue t
                    JOIN accounts a ON t.account_id = a.id
                    WHERE a.session_name LIKE $1
                )
                   OR account_id IN (
                    SELECT id FROM accounts WHERE session_name LIKE $1
                )
                """,
                session_name_like,
            )
            await conn.execute(
                """
                DELETE FROM task_attempts
                WHERE task_id IN (
                    SELECT t.id FROM task_queue t
                    JOIN accounts a ON t.account_id = a.id
                    WHERE a.session_name LIKE $1
                )
                   OR account_id IN (
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
            await conn.execute(
                """
                DELETE FROM task_attempts
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

        if dedup_key_like and session_name_like:
            await conn.execute(
                """
                DELETE FROM account_resource_usage
                WHERE task_id IN (
                    SELECT id FROM task_queue
                    WHERE dedup_key LIKE $1
                       OR account_id IN (
                           SELECT id FROM accounts WHERE session_name LIKE $2
                       )
                       OR source_account_id IN (
                           SELECT id FROM accounts WHERE session_name LIKE $2
                       )
                       OR target_account_id IN (
                           SELECT id FROM accounts WHERE session_name LIKE $2
                       )
                )
                   OR account_id IN (
                       SELECT id FROM accounts WHERE session_name LIKE $2
                   )
                """,
                dedup_key_like,
                session_name_like,
            )
            await conn.execute(
                """
                DELETE FROM task_attempts
                WHERE task_id IN (
                    SELECT id FROM task_queue
                    WHERE dedup_key LIKE $1
                       OR account_id IN (
                           SELECT id FROM accounts WHERE session_name LIKE $2
                       )
                       OR source_account_id IN (
                           SELECT id FROM accounts WHERE session_name LIKE $2
                       )
                       OR target_account_id IN (
                           SELECT id FROM accounts WHERE session_name LIKE $2
                       )
                )
                   OR account_id IN (
                       SELECT id FROM accounts WHERE session_name LIKE $2
                   )
                """,
                dedup_key_like,
                session_name_like,
            )

        if dedup_key_like:
            await conn.execute(
                """
                UPDATE task_queue
                SET status = 'failed',
                    locked_by = NULL,
                    locked_at = NULL,
                    locked_until = NULL,
                    run_after = now() + interval '365 days'
                WHERE dedup_key LIKE $1
                  AND status IN ('queued', 'scheduled', 'retry', 'in_progress')
                """,
                dedup_key_like,
            )
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
