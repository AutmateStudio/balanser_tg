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
        self.assertEqual(data.created_by, "discovery_api:discover")


if __name__ == "__main__":
    unittest.main()
