"""D8 — тесты USE_PG_QUEUE для async add-channels."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.testclient import TestClient


class PgQueueAddChannelsApiTests(unittest.TestCase):
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
        _jobs["pid"] = _ClumpJob(clump=clump, parser_id="pid")
        return clump

    @patch("discovery_api.parser_router.enqueue_parser_add_channels", new_callable=AsyncMock)
    def test_async_add_channels_use_pg_queue(self, mock_enqueue: AsyncMock) -> None:
        from discovery_api.queue.producer import EnqueueAddChannelsResult

        async def _fake_enqueue(**kwargs):
            return EnqueueAddChannelsResult(
                task_ids=[101, 102],
                action_id=kwargs["action_id"],
            )

        mock_enqueue.side_effect = _fake_enqueue
        self._make_running_job()
        client = self._make_client()

        resp = client.post(
            "/discovery-api/parser/pid/add-channels",
            json={"channel_list": ["@a", "@b"]},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["async_mode"])
        self.assertEqual(len(body["action_id"]), 32)
        self.assertEqual(body["task_ids"], [101, 102])
        mock_enqueue.assert_awaited_once()
        call_kwargs = mock_enqueue.await_args.kwargs
        self.assertEqual(call_kwargs["parser_id"], "pid")
        self.assertEqual(call_kwargs["channel_list"], ["@a", "@b"])
        self.assertIn("webhook_url", call_kwargs)
        self.assertTrue(str(call_kwargs["webhook_url"]).startswith("http://h"))
        self.assertEqual(body["action_id"], call_kwargs["action_id"])

    @patch("discovery_api.parser_router.enqueue_parser_add_channels", new_callable=AsyncMock)
    def test_async_add_channels_pg_dedup_returns_existing_ids(
        self, mock_enqueue: AsyncMock
    ) -> None:
        from discovery_api.queue.producer import EnqueueAddChannelsResult

        mock_enqueue.return_value = EnqueueAddChannelsResult(
            task_ids=[55, 55],
            action_id="dedup-action",
        )
        self._make_running_job()
        client = self._make_client()

        resp = client.post(
            "/discovery-api/parser/pid/add-channels",
            json={"channel_list": ["@dup", "@dup"]},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["task_ids"], [55, 55])


class ProducerUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_one_task_per_channel(self) -> None:
        from app_balance.queue.source_channels import SourceChannelsRepo
        from app_balance.queue.task_queue import EnqueueInput, EnqueueResult, TaskQueueRepo
        from discovery_api.queue.producer import enqueue_parser_add_channels

        with patch.object(
            TaskQueueRepo, "enqueue", new_callable=AsyncMock
        ) as mock_enqueue, patch.object(
            SourceChannelsRepo, "find_id_by_ref", new_callable=AsyncMock
        ) as mock_find:
            mock_find.side_effect = [101, 102]
            mock_enqueue.side_effect = [
                EnqueueResult(created=True, task_id=10),
                EnqueueResult(created=False, task_id=None, existing_task_id=11),
            ]

            result = await enqueue_parser_add_channels(
                parser_id="p1",
                channel_list=["@a", "@b"],
                webhook_url="http://wh",
                action_id="act-1",
            )

        self.assertEqual(result.task_ids, [10, 11])
        self.assertEqual(result.action_id, "act-1")
        self.assertEqual(mock_enqueue.await_count, 2)
        self.assertEqual(mock_find.await_count, 2)

        first_call: EnqueueInput = mock_enqueue.await_args_list[0].args[0]
        self.assertEqual(first_call.task_type_code, "parser_add_channel")
        self.assertEqual(first_call.dedup_key, "parser_add_channel:p1:a")
        self.assertEqual(first_call.channel_id, 101)
        self.assertEqual(first_call.payload["parser_id"], "p1")
        self.assertEqual(first_call.payload["channel_ref"], "@a")
        self.assertEqual(first_call.payload["action_id"], "act-1")
        self.assertEqual(first_call.payload["webhook_url"], "http://wh")
        self.assertEqual(first_call.created_by, "discovery_api:add-channels")

    async def test_enqueue_skips_empty_channels(self) -> None:
        from app_balance.queue.source_channels import SourceChannelsRepo
        from app_balance.queue.task_queue import EnqueueResult, TaskQueueRepo
        from discovery_api.queue.producer import enqueue_parser_add_channels

        with patch.object(
            TaskQueueRepo, "enqueue", new_callable=AsyncMock
        ) as mock_enqueue, patch.object(
            SourceChannelsRepo, "find_id_by_ref", new_callable=AsyncMock, return_value=999
        ):
            mock_enqueue.return_value = EnqueueResult(created=True, task_id=7)

            result = await enqueue_parser_add_channels(
                parser_id="p1",
                channel_list=["", "   ", "@ok"],
                action_id="act-2",
            )

        self.assertEqual(result.task_ids, [7])
        mock_enqueue.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
