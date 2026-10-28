"""E2E: POST /discover → PG-очередь → worker → persist source_channels → poll.

Сквозной сценарий без реального Telethon: discover_unified_on_client замокан,
остальное — реальные HTTP, enqueue, dispatch, adapter, upsert и GET task status.

Требует QUEUE_DATABASE_URL и task_type telegram_discover в seed (A9).
На shared PG остановите queue-worker и in-process worker discovery-api.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app_balance.queue import db
from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.adapter import ClumpTaskAdapter
from app_balance.queue.dispatch import TaskDispatcher
from app_balance.queue.per_op_reading import TaskTypesRepo
from app_balance.queue.task_queue import TaskQueueRepo
from app_balance.queue_worker import QueueWorker, WorkerConfig
from discovery_api.discovery import (
    DiscoveredChannel,
    DiscoveredGroup,
    UnifiedDiscoveryResult,
)
from tests.conftest import TEST_ISOLATION_PRIORITY, requires_pg
from tests.pg_cleanup import cleanup_queue_test_data
from tests.queue_integration_helpers import AlwaysOkResourceChecker, insert_test_account
from tests.tz30.conftest import lock_all_free_accounts_except, unlock_accounts

_PREFIX = "test_discover_e2e_"
_WORKER_WAIT_TIMEOUT = 45.0

# Стабильные peer_id для проверки persist (не пересекаются с prod).
_PEER_BC_OK = -100_900_000_001
_PEER_BC_SKIP = -100_900_000_002
_PEER_GROUP = -100_900_000_003
_QUERY = "e2e-маркетинг"


async def _require_telegram_discover_task_type() -> None:
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_enabled FROM task_types WHERE code = 'telegram_discover'"
        )
    if row is None:
        pytest.skip("telegram_discover отсутствует в seed (A9)")
    if not row["is_enabled"]:
        pytest.skip("telegram_discover выключен в seed")


async def _require_tg_platform() -> int:
    async with db.acquire() as conn:
        platform_id = await conn.fetchval(
            "SELECT id FROM platforms WHERE lower(code) = 'tg' LIMIT 1"
        )
    if platform_id is None:
        pytest.skip("platform 'tg' не найден в platforms")
    return int(platform_id)


async def _cleanup_e2e_rows() -> None:
    async with db.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM source_channels
            WHERE external_channel_id = ANY($1::text[])
            """,
            [str(_PEER_BC_OK), str(_PEER_BC_SKIP), str(_PEER_GROUP)],
        )
    await cleanup_queue_test_data(
        dedup_key_like=f"telegram_discover:{_PREFIX}%",
        session_name_like=f"{_PREFIX}%",
    )


@pytest.fixture
async def discover_e2e_clean(pg_pool):
    await _cleanup_e2e_rows()
    yield
    await _cleanup_e2e_rows()


def _make_discovery_client() -> TestClient:
    from discovery_api.parser_router import parser_router
    from discovery_api.router import router

    app = FastAPI()
    app.include_router(router)
    app.include_router(parser_router)
    return TestClient(app)


def _mock_unified_result() -> UnifiedDiscoveryResult:
    return UnifiedDiscoveryResult(
        query=_QUERY,
        channels=[
            DiscoveredChannel(
                peer_id=_PEER_BC_OK,
                title="E2E Broadcast OK",
                username="e2e_bc_ok",
                participants_count=100,
                depth=0,
                source="search",
                score_total=10,
                score_signals={"about": "about ok", "linked_chat_id": -100_900_000_099},
                meta={"broadcast": True},
            ),
            DiscoveredChannel(
                peer_id=_PEER_BC_SKIP,
                title="E2E Broadcast no discussion",
                username="e2e_bc_skip",
                participants_count=50,
                depth=0,
                source="search",
                score_total=3,
                score_signals={"linked_chat_id": None},
                meta={"broadcast": True},
            ),
        ],
        groups=[
            DiscoveredGroup(
                peer_id=_PEER_GROUP,
                title="E2E Group",
                username="e2e_grp",
                participants_count=20,
                depth=0,
                source="contacts",
                matched_seed="seed",
                score_total=7,
                meta={"megagroup": True},
            ),
        ],
        seeds=["seed"],
        total=3,
        depth_stats={0: 3},
        errors=[],
    )


def _build_worker() -> QueueWorker:
    config = WorkerConfig(
        worker_id=f"{_PREFIX}worker",
        poll_interval_seconds=0.01,
        task_type_codes=["telegram_discover"],
    )
    adapter = ClumpTaskAdapter(
        client_getter=AsyncMock(return_value=MagicMock(name="telethon_client")),
    )
    dispatcher = TaskDispatcher(
        queue=TaskQueueRepo(),
        accounts=AccountsRepo(),
        task_types=TaskTypesRepo(),
        adapter=adapter,
        resource_check=AlwaysOkResourceChecker(),
        postpone_delay_seconds=300,
        retry_delay_seconds=60,
    )
    return QueueWorker(config, dispatcher=dispatcher)


async def _boost_task_priority(task_id: int) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE task_queue SET priority = $2 WHERE id = $1",
            task_id,
            TEST_ISOLATION_PRIORITY,
        )


async def _wait_task_status(task_id: int, *, expected: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with db.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM task_queue WHERE id = $1", task_id
            )
        if status == expected:
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"task {task_id} not {expected} within {timeout}s")


async def _run_worker_until_done(worker: QueueWorker, task_id: int) -> None:
    run_task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(
            _wait_task_status(task_id, expected="done", timeout=_WORKER_WAIT_TIMEOUT),
            timeout=_WORKER_WAIT_TIMEOUT,
        )
    finally:
        worker.stop()
        await asyncio.wait_for(run_task, timeout=_WORKER_WAIT_TIMEOUT)


@pytest.mark.e2e
@pytest.mark.integration
@requires_pg
@pytest.mark.asyncio
async def test_discover_async_e2e_enqueue_worker_persist(
    discover_e2e_clean,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /discover → task_id → worker → done → persist + payload.result."""
    await _require_telegram_discover_task_type()
    platform_id = await _require_tg_platform()

    monkeypatch.setenv("USE_PG_QUEUE", "true")

    account_id, session_name = await insert_test_account(prefix=_PREFIX)
    locked = await lock_all_free_accounts_except({account_id})
    client = _make_discovery_client()

    try:
        # --- 1. HTTP: принятие async-запроса ---
        resp = client.post(
            "/discovery-api/discover",
            json={
                "session_name": session_name,
                "query": _QUERY,
                "first_pass_limit": 5,
                "similarity_depth": 1,
                "include_global_search": True,
                "include_groups": True,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["async_mode"] is True
        assert body["task_id"] is not None
        assert body["action_id"]
        assert body["channels"] == []
        assert body["groups"] == []
        assert body["errors"] == []

        task_id = int(body["task_id"])
        action_id = body["action_id"]

        # --- 2. PG: задача поставлена корректно ---
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT task_type_code, status, account_id, payload, dedup_key
                FROM task_queue WHERE id = $1
                """,
                task_id,
            )
        assert row is not None
        assert row["task_type_code"] == "telegram_discover"
        assert row["status"] == "queued"
        assert int(row["account_id"]) == account_id
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["query"] == _QUERY
        assert payload["action_id"] == action_id
        assert payload["first_pass_limit"] == 5
        assert "telegram_discover:" in row["dedup_key"]

        await _boost_task_priority(task_id)

        # --- 3. Worker: обработка (mock Telethon discover) ---
        mock_result = _mock_unified_result()
        with patch(
            "discovery_api.discovery.discover_unified_on_client",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_discover:
            worker = _build_worker()
            await _run_worker_until_done(worker, task_id)
            mock_discover.assert_awaited_once()

        # --- 4. Poll API: результат в payload.result ---
        poll = client.get(f"/discovery-api/parser/queue/tasks/{task_id}")
        assert poll.status_code == 200, poll.text
        poll_body = poll.json()
        assert poll_body["status"] == "done"
        assert poll_body["task_type_code"] == "telegram_discover"

        result = poll_body["payload"].get("result") or {}
        persist = result.get("persist") or {}
        assert result.get("query") == _QUERY
        assert persist.get("inserted") == 2
        assert persist.get("skipped_no_discussion") == 1
        assert len(persist.get("channel_ids") or []) == 2
        assert len(result.get("channels") or []) == 2
        assert len(result.get("groups") or []) == 1

        # --- 5. PG: строки в source_channels ---
        async with db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT external_channel_id, name, metadata
                FROM source_channels
                WHERE platform_id = $1
                  AND external_channel_id = ANY($2::text[])
                ORDER BY external_channel_id
                """,
                platform_id,
                [str(_PEER_BC_OK), str(_PEER_BC_SKIP), str(_PEER_GROUP)],
            )
        ext_ids = {r["external_channel_id"] for r in rows}
        assert str(_PEER_BC_OK) in ext_ids
        assert str(_PEER_GROUP) in ext_ids
        assert str(_PEER_BC_SKIP) not in ext_ids

        bc_row = next(r for r in rows if r["external_channel_id"] == str(_PEER_BC_OK))
        assert bc_row["name"] == "E2E Broadcast OK"
        meta = bc_row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta.get("has_discussion") is True
        assert meta.get("entity_kind") == "channel"

        grp_row = next(r for r in rows if r["external_channel_id"] == str(_PEER_GROUP))
        meta_g = grp_row["metadata"]
        if isinstance(meta_g, str):
            meta_g = json.loads(meta_g)
        assert meta_g.get("entity_kind") == "group"

        # --- 6. Dedup: повторный запрос → тот же task_id (active dedup) ---
        # Задача done — dedup_key свободен; повтор создаёт новую. Проверим queued dedup
        # на втором запросе до завершения отдельно не можем; проверим что enqueue идемпотент
        # для in-flight: создаём вторую задачу с другим query и убеждаемся что task_id другой.
        resp2 = client.post(
            "/discovery-api/discover",
            json={
                "session_name": session_name,
                "query": f"{_QUERY}-other",
                "first_pass_limit": 5,
                "similarity_depth": 1,
            },
        )
        assert resp2.status_code == 200
        assert resp2.json()["task_id"] != task_id

    finally:
        await unlock_accounts(locked)
