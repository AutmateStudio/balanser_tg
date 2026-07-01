"""D7 — unit-тесты channel_assignment_sync (без Telethon, без обязательной PG)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app_balance.queue.accounts import Account
from app_balance.queue.channel_assignment_sync import (
    pg_dual_write_enabled,
    sync_after_move_channel,
    sync_after_parser_add_channel,
    sync_after_parser_remove_channel,
)
from app_balance.queue.task_queue import ClaimedTask


def _account(account_id: int = 7) -> Account:
    return Account(
        id=account_id,
        session_name="/s1",
        status="active",
        is_enabled=True,
        current_task_id=None,
        cooldown_until=None,
        last_used_at=None,
    )


def _task(*, channel_id: int | None = 100) -> ClaimedTask:
    return ClaimedTask(
        id=42,
        task_type_id=1,
        task_type_code="parser_add_channel",
        priority=500,
        payload={},
        channel_id=channel_id,
        account_id=7,
        source_account_id=None,
        target_account_id=None,
        attempt_count=1,
        max_attempts=3,
        dedup_key=None,
        locked_by="test",
        locked_until=None,
    )


def test_pg_dual_write_enabled_requires_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUEUE_DATABASE_URL", raising=False)
    assert pg_dual_write_enabled() is False

    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    assert pg_dual_write_enabled() is True


@pytest.mark.asyncio
async def test_sync_add_noop_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUEUE_DATABASE_URL", raising=False)
    clump = MagicMock()
    clump._persist_safe = MagicMock()
    repo = AsyncMock()

    await sync_after_parser_add_channel(_task(), _account(), clump, repo=repo)

    repo.set_assigned_account.assert_not_awaited()
    clump._persist_safe.assert_called_once()


@pytest.mark.asyncio
async def test_sync_add_calls_set_assigned_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    clump = MagicMock()
    clump._persist_safe = MagicMock()
    repo = AsyncMock()
    repo.set_assigned_account = AsyncMock(return_value=True)

    with patch(
        "app_balance.queue.channel_assignment_sync._ensure_pool",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await sync_after_parser_add_channel(
            _task(channel_id=55),
            _account(7),
            clump,
            repo=repo,
        )

    repo.set_assigned_account.assert_awaited_once_with(55, 7)
    clump._persist_safe.assert_called_once()


@pytest.mark.asyncio
async def test_sync_add_resolves_channel_id_from_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    clump = MagicMock()
    clump._persist_safe = MagicMock()
    repo = AsyncMock()
    repo.find_id_by_ref = AsyncMock(return_value=55)
    repo.set_assigned_account = AsyncMock(return_value=True)
    task = _task(channel_id=None)
    task = ClaimedTask(
        id=task.id,
        task_type_id=task.task_type_id,
        task_type_code=task.task_type_code,
        priority=task.priority,
        payload={"channel_ref": "@mychannel"},
        channel_id=None,
        account_id=task.account_id,
        source_account_id=task.source_account_id,
        target_account_id=task.target_account_id,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        dedup_key=task.dedup_key,
        locked_by=task.locked_by,
        locked_until=task.locked_until,
    )

    with patch(
        "app_balance.queue.channel_assignment_sync._ensure_pool",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await sync_after_parser_add_channel(
            task,
            _account(),
            clump,
            repo=repo,
        )

    repo.find_id_by_ref.assert_awaited_once_with("@mychannel")
    repo.set_assigned_account.assert_awaited_once_with(55, 7)
    clump._persist_safe.assert_called_once()


@pytest.mark.asyncio
async def test_sync_add_skips_pg_when_channel_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    clump = MagicMock()
    clump._persist_safe = MagicMock()
    repo = AsyncMock()
    repo.find_id_by_ref = AsyncMock(return_value=None)

    with patch(
        "app_balance.queue.channel_assignment_sync._ensure_pool",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await sync_after_parser_add_channel(
            _task(channel_id=None),
            _account(),
            clump,
            repo=repo,
        )

    repo.set_assigned_account.assert_not_awaited()
    clump._persist_safe.assert_called_once()


@pytest.mark.asyncio
async def test_sync_add_raises_when_channel_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    clump = MagicMock()
    repo = AsyncMock()
    repo.set_assigned_account = AsyncMock(return_value=False)

    with patch(
        "app_balance.queue.channel_assignment_sync._ensure_pool",
        new_callable=AsyncMock,
        return_value=True,
    ):
        with pytest.raises(RuntimeError, match="source_channel_not_found:100"):
            await sync_after_parser_add_channel(
                _task(channel_id=100),
                _account(),
                clump,
                repo=repo,
            )


@pytest.mark.asyncio
async def test_sync_add_calls_persist_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUEUE_DATABASE_URL", raising=False)
    clump = MagicMock()
    clump._persist_safe = MagicMock()

    await sync_after_parser_add_channel(_task(), _account(), clump)

    clump._persist_safe.assert_called_once()


@pytest.mark.asyncio
async def test_sync_move_uses_target_account_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    clump = MagicMock()
    clump._persist_safe = MagicMock()
    repo = AsyncMock()
    repo.set_assigned_account = AsyncMock(return_value=True)
    move_task = ClaimedTask(
        id=99,
        task_type_id=2,
        task_type_code="move_channel",
        priority=100,
        payload={},
        channel_id=200,
        account_id=None,
        source_account_id=10,
        target_account_id=20,
        attempt_count=1,
        max_attempts=3,
        dedup_key=None,
        locked_by="test",
        locked_until=None,
    )

    with patch(
        "app_balance.queue.channel_assignment_sync._ensure_pool",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await sync_after_move_channel(
            move_task,
            _account(20),
            clump,
            repo=repo,
        )

    repo.set_assigned_account.assert_awaited_once_with(200, 20)
    clump._persist_safe.assert_called_once()


@pytest.mark.asyncio
async def test_sync_move_noop_on_missing_channel_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    clump = MagicMock()
    clump._persist_safe = MagicMock()
    repo = AsyncMock()
    move_task = _task(channel_id=None)
    move_task = ClaimedTask(
        id=99,
        task_type_id=2,
        task_type_code="move_channel",
        priority=100,
        payload={},
        channel_id=None,
        account_id=None,
        source_account_id=10,
        target_account_id=20,
        attempt_count=1,
        max_attempts=3,
        dedup_key=None,
        locked_by="test",
        locked_until=None,
    )

    with patch(
        "app_balance.queue.channel_assignment_sync._ensure_pool",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await sync_after_move_channel(
            move_task,
            _account(20),
            clump,
            repo=repo,
        )

    repo.set_assigned_account.assert_not_awaited()
    clump._persist_safe.assert_called_once()


@pytest.mark.asyncio
async def test_sync_add_persists_clump_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QUEUE_DATABASE_URL", raising=False)

    class _ClumpConfig:
        @staticmethod
        def overrides() -> dict[str, object]:
            return {}

    class ClumpWithAssignments:
        clump_name = "test"
        session_name_list = ["/s1"]
        webhook_url = ""
        assignments = {"@ch": "/s1"}
        account_meta: dict[str, dict[str, object]] = {}
        config = _ClumpConfig()

        def list_channels(self) -> list[str]:
            return []

        def all_allowed_chat_ids(self) -> set[int]:
            return set()

        def _persist_safe(self) -> None:
            from discovery_api.parser_store import clump_to_record, upsert_job

            upsert_job(
                clump_to_record(self, parser_id="p-test"),
            )

    clump = ClumpWithAssignments()

    with patch("discovery_api.parser_store.upsert_job") as mock_upsert:
        await sync_after_parser_add_channel(_task(), _account(), clump)

    mock_upsert.assert_called_once()
    record = mock_upsert.call_args.args[0]
    assert record["assignments"] == {"@ch": "/s1"}


@pytest.mark.asyncio
async def test_sync_remove_noop_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUEUE_DATABASE_URL", raising=False)
    clump = MagicMock()
    clump._persist_safe = MagicMock()
    repo = AsyncMock()

    await sync_after_parser_remove_channel(_task(), _account(), clump, repo=repo)

    repo.clear_assigned_account.assert_not_awaited()
    clump._persist_safe.assert_called_once()


@pytest.mark.asyncio
async def test_sync_remove_calls_clear_assigned_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    clump = MagicMock()
    clump._persist_safe = MagicMock()
    repo = AsyncMock()
    repo.clear_assigned_account = AsyncMock(return_value=True)

    with patch(
        "app_balance.queue.channel_assignment_sync._ensure_pool",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await sync_after_parser_remove_channel(
            _task(channel_id=55),
            _account(7),
            clump,
            repo=repo,
        )

    repo.clear_assigned_account.assert_awaited_once_with(55)
    clump._persist_safe.assert_called_once()


@pytest.mark.asyncio
async def test_sync_remove_resolves_channel_id_from_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    clump = MagicMock()
    clump._persist_safe = MagicMock()
    repo = AsyncMock()
    repo.find_id_by_ref = AsyncMock(return_value=55)
    repo.clear_assigned_account = AsyncMock(return_value=True)
    remove_task = ClaimedTask(
        id=99,
        task_type_id=3,
        task_type_code="parser_remove_channel",
        priority=400,
        payload={"channel_ref": "@mychannel"},
        channel_id=None,
        account_id=7,
        source_account_id=None,
        target_account_id=None,
        attempt_count=1,
        max_attempts=3,
        dedup_key=None,
        locked_by="test",
        locked_until=None,
    )

    with patch(
        "app_balance.queue.channel_assignment_sync._ensure_pool",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await sync_after_parser_remove_channel(
            remove_task,
            _account(),
            clump,
            repo=repo,
        )

    repo.find_id_by_ref.assert_awaited_once_with("@mychannel")
    repo.clear_assigned_account.assert_awaited_once_with(55)
    clump._persist_safe.assert_called_once()


@pytest.mark.asyncio
async def test_sync_remove_skips_pg_when_channel_id_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    clump = MagicMock()
    clump._persist_safe = MagicMock()
    repo = AsyncMock()

    with patch(
        "app_balance.queue.channel_assignment_sync._ensure_pool",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await sync_after_parser_remove_channel(
            _task(channel_id=None),
            _account(),
            clump,
            repo=repo,
        )

    repo.clear_assigned_account.assert_not_awaited()
    clump._persist_safe.assert_called_once()


@pytest.mark.asyncio
async def test_sync_remove_warns_when_channel_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_DATABASE_URL", "postgresql://u:p@localhost/db")
    clump = MagicMock()
    clump._persist_safe = MagicMock()
    repo = AsyncMock()
    repo.clear_assigned_account = AsyncMock(return_value=False)

    with patch(
        "app_balance.queue.channel_assignment_sync._ensure_pool",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await sync_after_parser_remove_channel(
            _task(channel_id=100),
            _account(),
            clump,
            repo=repo,
        )

    repo.clear_assigned_account.assert_awaited_once_with(100)
    clump._persist_safe.assert_called_once()
