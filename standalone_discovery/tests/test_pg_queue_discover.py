"""PG queue — async discover (telegram_discover)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.testclient import TestClient


class PgQueueDiscoverApiTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["USE_PG_QUEUE"] = "true"

    def tearDown(self) -> None:
        os.environ.pop("USE_PG_QUEUE", None)

    def _make_client(self) -> TestClient:
        from discovery_api.router import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    @patch("discovery_api.router.enqueue_telegram_discover", new_callable=AsyncMock)
    def test_async_discover_use_pg_queue(self, mock_enqueue: AsyncMock) -> None:
        from discovery_api.queue.producer import EnqueueTelegramDiscoverResult

        async def _fake_enqueue(**kwargs):
            return EnqueueTelegramDiscoverResult(task_id=701, action_id=kwargs["action_id"])

        mock_enqueue.side_effect = _fake_enqueue
        client = self._make_client()

        resp = client.post(
            "/discovery-api/discover",
            json={
                "session_name": "Client1",
                "query": "маркетинг",
                "first_pass_limit": 20,
                "similarity_depth": 2,
            },
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["async_mode"])
        self.assertEqual(body["task_id"], 701)
        self.assertEqual(body["channels"], [])
        self.assertEqual(body["groups"], [])
        mock_enqueue.assert_awaited_once()

    @patch("discovery_api.router.enqueue_telegram_discover", new_callable=AsyncMock)
    def test_async_discover_account_missing(self, mock_enqueue: AsyncMock) -> None:
        from discovery_api.queue.producer import EnqueueTelegramDiscoverResult

        mock_enqueue.return_value = EnqueueTelegramDiscoverResult(
            task_id=None, action_id="missing"
        )
        client = self._make_client()

        resp = client.post(
            "/discovery-api/discover",
            json={"session_name": "Unknown", "query": "test"},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["async_mode"])
        self.assertEqual(len(body["errors"]), 1)

    @patch("discovery_api.router.persist_unified_discovery", new_callable=AsyncMock)
    @patch("discovery_api.router.discover_unified_on_client", new_callable=AsyncMock)
    @patch("discovery_api.router.get_or_create_client", new_callable=AsyncMock)
    def test_sync_discover_persists(
        self,
        mock_client: AsyncMock,
        mock_discover: AsyncMock,
        mock_persist: AsyncMock,
    ) -> None:
        from discovery_api.discovery import DiscoveredChannel, UnifiedDiscoveryResult
        from app_balance.queue.discover_persist import PersistStats

        mock_client.return_value = MagicMock()
        mock_discover.return_value = UnifiedDiscoveryResult(
            query="ремонт",
            channels=[
                DiscoveredChannel(
                    peer_id=1,
                    title="Ch",
                    username="ch",
                    participants_count=10,
                    depth=0,
                    source="search",
                    meta={"megagroup": True},
                )
            ],
            total=1,
            depth_stats={0: 1},
        )
        mock_persist.return_value = PersistStats(inserted=1, updated=0, skipped_no_discussion=0)

        client = self._make_client()
        resp = client.post(
            "/discovery-api/discover?async=false",
            json={"session_name": "Client1", "query": "ремонт"},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["async_mode"])
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["persist"]["inserted"], 1)
        mock_persist.assert_awaited_once()

    @patch("discovery_api.router.enqueue_telegram_discover", new_callable=AsyncMock)
    def test_async_discover_without_session_name_auto_picks(
        self, mock_enqueue: AsyncMock
    ) -> None:
        """POST /discover без session_name (async + PG queue) — auto-pick аккаунта."""
        from discovery_api.queue.producer import EnqueueTelegramDiscoverResult

        async def _fake_enqueue(**kwargs):
            self.assertIsNone(kwargs.get("session_name"))
            return EnqueueTelegramDiscoverResult(task_id=702, action_id=kwargs["action_id"])

        mock_enqueue.side_effect = _fake_enqueue
        client = self._make_client()

        resp = client.post(
            "/discovery-api/discover",
            json={"query": "маркетинг", "first_pass_limit": 20, "similarity_depth": 2},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["async_mode"])
        self.assertEqual(body["task_id"], 702)
        mock_enqueue.assert_awaited_once()

    def test_sync_discover_without_session_name_rejected(self) -> None:
        """Синхронный режим (async=false) без session_name — 400, нет диспетчера."""
        client = self._make_client()

        resp = client.post(
            "/discovery-api/discover?async=false",
            json={"query": "ремонт"},
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("session_name", resp.json()["detail"])

    def test_async_discover_without_session_name_no_pg_queue_rejected(self) -> None:
        """async=true, но USE_PG_QUEUE=false — падает в синхронный путь, требует session_name."""
        os.environ["USE_PG_QUEUE"] = "false"
        client = self._make_client()

        resp = client.post(
            "/discovery-api/discover",
            json={"query": "ремонт"},
        )

        self.assertEqual(resp.status_code, 400)

    @patch("discovery_api.router.discover", new_callable=AsyncMock)
    def test_discover_groups_deprecated_wrapper(self, mock_discover: AsyncMock) -> None:
        from discovery_api.router import DiscoveryResponse, PersistStatsResponse

        mock_discover.return_value = DiscoveryResponse(
            query="word",
            total=0,
            depth_stats={},
            channels=[],
            groups=[],
            async_mode=True,
            task_id=99,
            action_id="act",
            deprecated=False,
        )
        client = self._make_client()

        resp = client.post(
            "/discovery-api/discover-groups",
            json={"session_name": "Client1", "word": "word", "limit": 10, "depth": 1},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["deprecated"])
        mock_discover.assert_awaited_once()


class ProducerTelegramDiscoverUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_telegram_discover_fixed_account(self) -> None:
        from app_balance.queue.task_queue import EnqueueInput, EnqueueResult, TaskQueueRepo
        from discovery_api.queue.producer import enqueue_telegram_discover

        with patch.object(
            TaskQueueRepo, "enqueue", new_callable=AsyncMock
        ) as mock_enqueue, patch(
            "discovery_api.queue.producer.AccountsRepo"
        ) as accounts_cls:
            accounts_cls.return_value.get_id_by_session_name = AsyncMock(return_value=42)
            mock_enqueue.return_value = EnqueueResult(created=True, task_id=99)

            result = await enqueue_telegram_discover(
                session_name="/app/sessions/Client1",
                query="маркетинг",
                first_pass_limit=15,
                similarity_depth=1,
                include_global_search=True,
                include_groups=True,
                action_id="act-td",
            )

        self.assertEqual(result.task_id, 99)
        data: EnqueueInput = mock_enqueue.await_args.args[0]
        self.assertEqual(data.task_type_code, "telegram_discover")
        self.assertEqual(data.account_id, 42)
        self.assertEqual(data.payload["query"], "маркетинг")
        self.assertEqual(data.payload["session_name"], "Client1")
        self.assertEqual(data.created_by, "discovery_api:discover")
        self.assertTrue(data.dedup_key.startswith("telegram_discover:Client1:"))

    async def test_enqueue_telegram_discover_auto_pick_when_session_omitted(self) -> None:
        """session_name не задан — account_id=None, dispatch() сам подберёт аккаунт."""
        from app_balance.queue.task_queue import EnqueueInput, EnqueueResult, TaskQueueRepo
        from discovery_api.queue.producer import enqueue_telegram_discover

        with patch.object(
            TaskQueueRepo, "enqueue", new_callable=AsyncMock
        ) as mock_enqueue, patch(
            "discovery_api.queue.producer.AccountsRepo"
        ) as accounts_cls:
            mock_enqueue.return_value = EnqueueResult(created=True, task_id=100)

            result = await enqueue_telegram_discover(
                query="маркетинг",
                first_pass_limit=15,
                similarity_depth=1,
                include_global_search=True,
                include_groups=True,
                action_id="act-auto",
            )

        self.assertEqual(result.task_id, 100)
        # AccountsRepo не должен даже создаваться — резолва аккаунта нет вовсе.
        accounts_cls.assert_not_called()
        data: EnqueueInput = mock_enqueue.await_args.args[0]
        self.assertEqual(data.task_type_code, "telegram_discover")
        self.assertIsNone(data.account_id)
        self.assertNotIn("session_name", data.payload)
        self.assertEqual(data.payload["query"], "маркетинг")
        self.assertTrue(data.dedup_key.startswith("telegram_discover:auto:"))

    async def test_enqueue_telegram_discover_auto_pick_with_empty_session(self) -> None:
        """session_name="" (пустая строка) — тоже трактуется как auto-pick."""
        from app_balance.queue.task_queue import EnqueueInput, EnqueueResult, TaskQueueRepo
        from discovery_api.queue.producer import enqueue_telegram_discover

        with patch.object(
            TaskQueueRepo, "enqueue", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = EnqueueResult(created=True, task_id=101)

            result = await enqueue_telegram_discover(
                session_name="   ",
                query="реклама",
                first_pass_limit=10,
                similarity_depth=2,
                include_global_search=True,
                include_groups=False,
                action_id="act-blank",
            )

        self.assertEqual(result.task_id, 101)
        data: EnqueueInput = mock_enqueue.await_args.args[0]
        self.assertIsNone(data.account_id)
        self.assertNotIn("session_name", data.payload)

    async def test_dedup_key_differs_for_auto_vs_fixed(self) -> None:
        from discovery_api.queue.producer import _telegram_discover_dedup_key

        fixed = _telegram_discover_dedup_key(
            "Client1",
            "запрос",
            first_pass_limit=10,
            similarity_depth=2,
            include_global_search=True,
            include_groups=True,
        )
        auto = _telegram_discover_dedup_key(
            None,
            "запрос",
            first_pass_limit=10,
            similarity_depth=2,
            include_global_search=True,
            include_groups=True,
        )
        self.assertNotEqual(fixed, auto)
        self.assertIn(":Client1:", fixed)
        self.assertIn(":auto:", auto)


if __name__ == "__main__":
    unittest.main()
