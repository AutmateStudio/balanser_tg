"""HTTP-тесты action queue и async add-channels."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.testclient import TestClient


class AsyncAddChannelsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        os.environ["PARSER_PERSISTENCE_ENABLED"] = "0"
        os.environ["USE_PG_QUEUE"] = "false"
        os.environ["ACTION_QUEUE_DB"] = os.path.join(self._tmpdir, "q.db")
        from discovery_api.action_queue import reset_action_queue_for_tests

        reset_action_queue_for_tests()

    def tearDown(self) -> None:
        from discovery_api.parser_router import _jobs

        _jobs.clear()
        os.environ.pop("PARSER_PERSISTENCE_ENABLED", None)
        os.environ.pop("USE_PG_QUEUE", None)
        os.environ.pop("ACTION_QUEUE_DB", None)

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

    def test_async_add_channels_enqueues_action(self) -> None:
        from discovery_api.action_queue import get_action

        self._make_running_job()
        client = self._make_client()
        resp = client.post(
            "/discovery-api/parser/pid/add-channels",
            json={"channel_list": ["@a", "@b"]},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["async_mode"])
        self.assertIsNotNone(body.get("action_id"))
        action = get_action(body["action_id"])
        self.assertIsNotNone(action)
        self.assertEqual(action["status"], "queued")
        self.assertEqual(action["progress"]["total"], 2)
        self.assertEqual(action["parser_id"], "pid")

    def test_sync_add_channels_with_async_zero(self) -> None:
        clump = self._make_running_job()
        client = self._make_client()

        with patch.object(
            clump, "add_channels_batch", new_callable=AsyncMock, return_value={
                "channel_list": ["@a"],
                "added": ["@a"],
                "already_present": [],
                "errors": [],
                "pending": [],
            }
        ), patch.object(clump, "start", new_callable=AsyncMock):
            resp = client.post(
                "/discovery-api/parser/pid/add-channels?async=0",
                json={"channel_list": ["@a"]},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["async_mode"])
        self.assertEqual(body["added"], ["@a"])

    def test_actions_list_and_get(self) -> None:
        from discovery_api.action_queue import enqueue_action

        self._make_running_job()
        client = self._make_client()
        action = enqueue_action(
            action_type="add_channels",
            parser_id="pid",
            payload={"channel_list": ["@x", "@y"]},
        )

        list_resp = client.get("/discovery-api/parser/actions?parser_id=pid")
        self.assertEqual(list_resp.status_code, 200)
        data = list_resp.json()
        self.assertGreaterEqual(data["total"], 1)
        ids = {a["id"] for a in data["actions"]}
        self.assertIn(action["id"], ids)

        get_resp = client.get(f"/discovery-api/parser/actions/{action['id']}")
        self.assertEqual(get_resp.status_code, 200)
        item = get_resp.json()
        self.assertEqual(item["id"], action["id"])
        self.assertEqual(item["progress"]["total"], 2)

    def test_action_get_404(self) -> None:
        client = self._make_client()
        resp = client.get("/discovery-api/parser/actions/nonexistent")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
