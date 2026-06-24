"""D9 — тесты USE_PG_QUEUE для async remove-channels."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.testclient import TestClient


class PgQueueRemoveChannelsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = os.path.join(os.path.dirname(__file__), ".tmp_pg_queue_tests")
        os.makedirs(self._tmpdir, exist_ok=True)
        os.environ["PARSER_PERSISTENCE_ENABLED"] = "0"
        os.environ["USE_PG_QUEUE"] = "true"
        os.environ["ACTION_QUEUE_DB"] = os.path.join(self._tmpdir, "q.db")

    def tearDown(self) -> None:
        from discovery_api.parser_router import _jobs

        _jobs.clear()
        for key in ("PARSER_PERSISTENCE_ENABLED", "USE_PG_QUEUE", "ACTION_QUEUE_DB"):
            os.environ.pop(key, None)

    def _make_client(self) -> TestClient:
        from discovery_api.parser_router import parser_router

        app = FastAPI()
        app.include_router(parser_router)
        return TestClient(app)

    def _make_running_job(self):
        from discovery_api.parser_router import _ClumpJob, _jobs
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        clump.parser_client_list[0].health.mark_connected()
        clump.assignments["@a"] = "/s1"
        _jobs["pid"] = _ClumpJob(clump=clump, parser_id="pid")
        return clump

    @patch(
        "discovery_api.parser_router.enqueue_parser_remove_channels",
        new_callable=AsyncMock,
    )
    def test_async_remove_channels_use_pg_queue(self, mock_enqueue: AsyncMock) -> None:
        from discovery_api.queue.producer import EnqueueRemoveChannelsResult

        async def _fake_enqueue(**kwargs):
            return EnqueueRemoveChannelsResult(
                task_ids=[201, 202],
                action_id=kwargs["action_id"],
            )

        mock_enqueue.side_effect = _fake_enqueue
        self._make_running_job()
        client = self._make_client()

        resp = client.post(
            "/discovery-api/parser/pid/remove-channels",
            json={"channel_list": ["@a", "@b"]},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["async_mode"])
        self.assertEqual(len(body["action_id"]), 32)
        self.assertEqual(body["task_ids"], [201, 202])
        mock_enqueue.assert_awaited_once()
        call_kwargs = mock_enqueue.await_args.kwargs
        self.assertEqual(call_kwargs["parser_id"], "pid")
        self.assertEqual(call_kwargs["channel_list"], ["@a", "@b"])
        self.assertEqual(body["action_id"], call_kwargs["action_id"])

    @patch(
        "discovery_api.parser_router.enqueue_parser_remove_channels",
        new_callable=AsyncMock,
    )
    def test_async_remove_channels_sync_mode_skips_pg(
        self, mock_enqueue: AsyncMock
    ) -> None:
        from discovery_api.parser_router import _ClumpJob, _jobs
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        clump.parser_client_list[0].health.mark_connected()
        _jobs["pid"] = _ClumpJob(clump=clump, parser_id="pid")
        client = self._make_client()

        with patch.object(
            SessionClump, "remove_channels_batch", new_callable=AsyncMock
        ) as mock_batch:
            mock_batch.return_value = {
                "channel_list": [],
                "removed": ["@x"],
                "not_found": [],
                "errors": [],
            }
            resp = client.post(
                "/discovery-api/parser/pid/remove-channels?async=false",
                json={"channel_list": ["@x"]},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["async_mode"])
        self.assertEqual(body["removed"], ["@x"])
        mock_enqueue.assert_not_awaited()
        mock_batch.assert_awaited_once()


class ProducerRemoveUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_one_task_per_channel_with_owner(self) -> None:
        from app_balance.queue.task_queue import EnqueueInput, EnqueueResult, TaskQueueRepo
        from discovery_api.queue.producer import enqueue_parser_remove_channels

        clump = MagicMock()
        clump.assignments = {"@a": "/s1", "@b": "/s1"}

        with (
            patch(
                "discovery_api.session_registry.get_clump",
                return_value=clump,
            ),
            patch.object(
                TaskQueueRepo, "enqueue", new_callable=AsyncMock
            ) as mock_enqueue,
            patch(
                "app_balance.queue.accounts.AccountsRepo.get_id_by_session_name",
                new_callable=AsyncMock,
                return_value=5,
            ),
            patch(
                "app_balance.queue.source_channels.SourceChannelsRepo.find_id_by_ref",
                new_callable=AsyncMock,
                side_effect=[10, 11],
            ),
        ):
            mock_enqueue.side_effect = [
                EnqueueResult(created=True, task_id=20),
                EnqueueResult(created=False, task_id=None, existing_task_id=21),
            ]

            result = await enqueue_parser_remove_channels(
                parser_id="p1",
                channel_list=["@a", "@b"],
                action_id="act-rm",
            )

        self.assertEqual(result.task_ids, [20, 21])
        self.assertEqual(result.action_id, "act-rm")
        self.assertEqual(mock_enqueue.await_count, 2)

        first_call: EnqueueInput = mock_enqueue.await_args_list[0].args[0]
        self.assertEqual(first_call.task_type_code, "parser_remove_channel")
        self.assertEqual(first_call.dedup_key, "parser_remove_channel:p1:a")
        self.assertEqual(first_call.payload["parser_id"], "p1")
        self.assertEqual(first_call.payload["channel_ref"], "@a")
        self.assertEqual(first_call.payload["action_id"], "act-rm")
        self.assertEqual(first_call.created_by, "discovery_api:remove-channels")
        self.assertEqual(first_call.account_id, 5)
        self.assertEqual(first_call.channel_id, 10)

    async def test_enqueue_skips_channel_without_owner(self) -> None:
        from app_balance.queue.task_queue import EnqueueResult, TaskQueueRepo
        from discovery_api.queue.producer import enqueue_parser_remove_channels

        clump = MagicMock()
        clump.assignments = {"@known": "/s1"}
        clump._find_owner = MagicMock(return_value=None)
        clump.parser_client_list = []

        with (
            patch(
                "discovery_api.session_registry.get_clump",
                return_value=clump,
            ),
            patch.object(
                TaskQueueRepo, "enqueue", new_callable=AsyncMock
            ) as mock_enqueue,
            patch(
                "app_balance.queue.accounts.AccountsRepo.get_id_by_session_name",
                new_callable=AsyncMock,
                return_value=5,
            ),
            patch(
                "app_balance.queue.source_channels.SourceChannelsRepo.find_id_by_ref",
                new_callable=AsyncMock,
                return_value=99,
            ),
        ):
            mock_enqueue.return_value = EnqueueResult(created=True, task_id=30)

            result = await enqueue_parser_remove_channels(
                parser_id="p1",
                channel_list=["@unknown", "@known"],
                action_id="act-2",
            )

        self.assertEqual(result.task_ids, [30])
        mock_enqueue.assert_awaited_once()

    async def test_enqueue_empty_when_clump_missing(self) -> None:
        from discovery_api.queue.producer import enqueue_parser_remove_channels

        with patch("discovery_api.session_registry.get_clump", return_value=None):
            result = await enqueue_parser_remove_channels(
                parser_id="p1",
                channel_list=["@a"],
                action_id="act-3",
            )

        self.assertEqual(result.task_ids, [])
        self.assertEqual(result.action_id, "act-3")


if __name__ == "__main__":
    unittest.main()
