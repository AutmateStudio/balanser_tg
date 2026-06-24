"""D3/D4 — unit-тесты ClumpTaskAdapter + execute_task (без Telethon)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app_balance.queue.accounts import Account
from app_balance.queue.adapter import ClumpTaskAdapter, execute_task
from app_balance.queue.task_queue import ClaimedTask


def _account(account_id: int = 1, session_name: str = "/s1") -> Account:
    return Account(
        id=account_id,
        session_name=session_name,
        status="active",
        is_enabled=True,
        current_task_id=None,
        cooldown_until=None,
        last_used_at=None,
    )


def _claimed(
    *,
    task_type_code: str = "parser_add_channel",
    payload: dict | None = None,
    source_account_id: int | None = None,
    target_account_id: int | None = None,
) -> ClaimedTask:
    return ClaimedTask(
        id=42,
        task_type_id=1,
        task_type_code=task_type_code,
        priority=500,
        payload=payload or {},
        channel_id=None,
        account_id=1,
        source_account_id=source_account_id,
        target_account_id=target_account_id,
        attempt_count=1,
        max_attempts=3,
        dedup_key=None,
        locked_by="test",
        locked_until=None,
    )


def _move_claimed(
    *,
    payload: dict | None = None,
    source_account_id: int = 10,
    target_account_id: int = 20,
) -> ClaimedTask:
    return _claimed(
        task_type_code="move_channel",
        payload=payload
        or {
            "parser_id": "p1",
            "channel_ref": "@ch",
        },
        source_account_id=source_account_id,
        target_account_id=target_account_id,
    )


def _move_accounts() -> dict[int, Account]:
    return {
        10: _account(10, "/src"),
        20: _account(20, "/tgt"),
    }


async def _account_getter(accounts: dict[int, Account]):
    async def getter(account_id: int) -> Account | None:
        return accounts.get(account_id)

    return getter


class FakeClump:
    def __init__(self) -> None:
        self.add_channel_on_session = AsyncMock(
            return_value={
                "channel": "@ch",
                "session_name": "/s1",
                "chat_id": 100,
                "error": None,
                "already_present": False,
            }
        )
        self.move_channel = AsyncMock(
            return_value={
                "channel": "@ch",
                "from_session": "/src",
                "to_session": "/tgt",
                "session_name": "/tgt",
                "chat_id": 100,
                "error": None,
                "moved": True,
            }
        )
        self.start = AsyncMock()


@pytest.mark.asyncio
async def test_execute_with_channel_ref_and_parser_id() -> None:
    clump = FakeClump()
    task = _claimed(
        payload={
            "parser_id": "p1",
            "channel_ref": "@ch",
            "webhook_url": "https://hook.example/",
        }
    )

    await execute_task(task, account=_account(), clump_getter=lambda _pid: clump)

    clump.add_channel_on_session.assert_awaited_once_with(
        "/s1",
        "@ch",
        webhook_url="https://hook.example/",
    )
    clump.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_accepts_ref_alias() -> None:
    clump = FakeClump()
    task = _claimed(payload={"parser_id": "p1", "ref": "@alias"})

    await execute_task(task, account=_account(), clump_getter=lambda _pid: clump)

    clump.add_channel_on_session.assert_awaited_once_with(
        "/s1",
        "@alias",
        webhook_url=None,
    )


@pytest.mark.asyncio
async def test_already_present_is_success() -> None:
    clump = FakeClump()
    clump.add_channel_on_session.return_value = {
        "channel": "@dup",
        "session_name": "/s1",
        "chat_id": 1,
        "error": None,
        "already_present": True,
    }
    task = _claimed(payload={"parser_id": "p1", "channel_ref": "@dup"})

    await execute_task(task, account=_account(), clump_getter=lambda _pid: clump)


@pytest.mark.asyncio
async def test_clump_error_raises() -> None:
    clump = FakeClump()
    clump.add_channel_on_session.return_value = {
        "channel": "@bad",
        "session_name": "/s1",
        "chat_id": None,
        "error": "FloodWait",
    }
    task = _claimed(payload={"parser_id": "p1", "channel_ref": "@bad"})

    with pytest.raises(RuntimeError, match="FloodWait"):
        await execute_task(task, account=_account(), clump_getter=lambda _pid: clump)


@pytest.mark.asyncio
async def test_missing_clump_raises() -> None:
    task = _claimed(payload={"parser_id": "p1", "channel_ref": "@ch"})

    with pytest.raises(RuntimeError, match="clump_not_loaded:p1"):
        await execute_task(task, account=_account(), clump_getter=lambda _pid: None)


@pytest.mark.asyncio
async def test_missing_parser_id_raises() -> None:
    task = _claimed(payload={"channel_ref": "@ch"})

    with pytest.raises(ValueError, match="missing parser_id"):
        await execute_task(task, account=_account(), clump_getter=lambda _pid: FakeClump())


@pytest.mark.asyncio
async def test_empty_channel_raises() -> None:
    task = _claimed(payload={"parser_id": "p1", "channel_ref": "  "})

    with pytest.raises(ValueError, match="missing channel_ref"):
        await execute_task(task, account=_account(), clump_getter=lambda _pid: FakeClump())


@pytest.mark.asyncio
async def test_invalid_webhook_url_raises() -> None:
    task = _claimed(payload={"parser_id": "p1", "channel_ref": "@ch", "webhook_url": 123})

    with pytest.raises(ValueError, match="invalid webhook_url"):
        await execute_task(task, account=_account(), clump_getter=lambda _pid: FakeClump())


@pytest.mark.asyncio
async def test_move_channel_happy_path() -> None:
    clump = FakeClump()
    task = _move_claimed(
        payload={
            "parser_id": "p1",
            "channel_ref": "@move",
            "webhook_url": "https://hook.example/",
        }
    )
    getter = await _account_getter(_move_accounts())

    await execute_task(
        task,
        account=_account(20, "/tgt"),
        clump_getter=lambda _pid: clump,
        account_getter=getter,
    )

    clump.move_channel.assert_awaited_once_with(
        "@move",
        "/src",
        "/tgt",
        webhook_url="https://hook.example/",
    )
    clump.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_move_channel_accepts_ref_alias() -> None:
    clump = FakeClump()
    task = _move_claimed(payload={"parser_id": "p1", "ref": "@alias"})
    getter = await _account_getter(_move_accounts())

    await execute_task(
        task,
        account=_account(20, "/tgt"),
        clump_getter=lambda _pid: clump,
        account_getter=getter,
    )

    clump.move_channel.assert_awaited_once_with(
        "@alias",
        "/src",
        "/tgt",
        webhook_url=None,
    )


@pytest.mark.asyncio
async def test_move_channel_already_present_is_success() -> None:
    clump = FakeClump()
    clump.move_channel.return_value = {
        "channel": "@here",
        "from_session": "/src",
        "to_session": "/tgt",
        "session_name": "/tgt",
        "chat_id": 1,
        "error": None,
        "already_present": True,
        "moved": False,
    }
    task = _move_claimed()
    getter = await _account_getter(_move_accounts())

    await execute_task(
        task,
        account=_account(20, "/tgt"),
        clump_getter=lambda _pid: clump,
        account_getter=getter,
    )


@pytest.mark.asyncio
async def test_move_channel_clump_error_raises() -> None:
    clump = FakeClump()
    clump.move_channel.return_value = {
        "channel": "@bad",
        "from_session": "/src",
        "to_session": "/tgt",
        "session_name": None,
        "chat_id": None,
        "error": "unexpected_owner",
    }
    task = _move_claimed()
    getter = await _account_getter(_move_accounts())

    with pytest.raises(RuntimeError, match="unexpected_owner"):
        await execute_task(
            task,
            account=_account(20, "/tgt"),
            clump_getter=lambda _pid: clump,
            account_getter=getter,
        )


@pytest.mark.asyncio
async def test_move_channel_missing_dual_account_ids_raises() -> None:
    task = _move_claimed(source_account_id=10, target_account_id=None)

    with pytest.raises(ValueError, match="missing dual account ids"):
        await execute_task(
            task,
            account=_account(20, "/tgt"),
            clump_getter=lambda _pid: FakeClump(),
            account_getter=await _account_getter(_move_accounts()),
        )


@pytest.mark.asyncio
async def test_move_channel_account_not_found_raises() -> None:
    clump = FakeClump()
    task = _move_claimed()
    getter = await _account_getter({10: _account(10, "/src")})

    with pytest.raises(RuntimeError, match="account_not_found:20"):
        await execute_task(
            task,
            account=_account(20, "/tgt"),
            clump_getter=lambda _pid: clump,
            account_getter=getter,
        )


@pytest.mark.asyncio
async def test_move_channel_missing_clump_raises() -> None:
    task = _move_claimed()
    getter = await _account_getter(_move_accounts())

    with pytest.raises(RuntimeError, match="clump_not_loaded:p1"):
        await execute_task(
            task,
            account=_account(20, "/tgt"),
            clump_getter=lambda _pid: None,
            account_getter=getter,
        )


@pytest.mark.asyncio
async def test_clump_task_adapter_delegates_add() -> None:
    clump = FakeClump()
    adapter = ClumpTaskAdapter(clump_getter=lambda _pid: clump)
    task = _claimed(payload={"parser_id": "p1", "ref": "@via_adapter"})

    await adapter.execute(task, account=_account(1, "/s2"))

    clump.add_channel_on_session.assert_awaited_once_with(
        "/s2",
        "@via_adapter",
        webhook_url=None,
    )


@pytest.mark.asyncio
async def test_clump_task_adapter_delegates_move() -> None:
    clump = FakeClump()
    accounts = _move_accounts()
    adapter = ClumpTaskAdapter(
        clump_getter=lambda _pid: clump,
        account_getter=(await _account_getter(accounts)),
    )
    task = _move_claimed(payload={"parser_id": "p1", "ref": "@via_adapter"})

    await adapter.execute(task, account=accounts[20])

    clump.move_channel.assert_awaited_once_with(
        "@via_adapter",
        "/src",
        "/tgt",
        webhook_url=None,
    )


@pytest.mark.asyncio
async def test_queue_prot_reexport() -> None:
    from discovery_api.queue_prot import ClumpTaskAdapter as ExportedAdapter
    from discovery_api.queue_prot import execute_task as exported_execute

    assert ExportedAdapter is ClumpTaskAdapter
    assert exported_execute is execute_task
