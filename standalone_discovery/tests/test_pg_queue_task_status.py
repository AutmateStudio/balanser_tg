"""D10 — тесты GET /queue/tasks/{task_id}."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.testclient import TestClient


class PgQueueTaskStatusApiTests(unittest.TestCase):
    def _make_client(self) -> TestClient:
        from discovery_api.parser_router import parser_router

        app = FastAPI()
        app.include_router(parser_router)
        return TestClient(app)

    @patch("discovery_api.parser_router.get_task_snapshot", new_callable=AsyncMock)
    def test_get_task_ok(self, mock_get: AsyncMock) -> None:
        from app_balance.queue.task_queue import TaskSnapshot

        mock_get.return_value = TaskSnapshot(
            id=101,
            task_type_code="parser_add_channel",
            status="queued",
            attempt_count=0,
            postpone_count=0,
            last_error=None,
            last_error_code=None,
            payload={"parser_id": "pid", "channel_ref": "@a", "action_id": "act-1"},
            run_after=datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc),
            started_at=None,
            finished_at=None,
            last_error_at=None,
        )
        client = self._make_client()

        resp = client.get("/discovery-api/parser/queue/tasks/101")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["id"], 101)
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["attempt_count"], 0)
        self.assertEqual(body["postpone_count"], 0)
        self.assertIsNone(body["last_error"])
        self.assertIsNone(body["last_error_code"])
        self.assertEqual(body["payload"]["action_id"], "act-1")
        mock_get.assert_awaited_once_with(101)

    @patch("discovery_api.parser_router.get_task_snapshot", new_callable=AsyncMock)
    def test_get_task_maps_payload(self, mock_get: AsyncMock) -> None:
        from app_balance.queue.task_queue import TaskSnapshot

        mock_get.return_value = TaskSnapshot(
            id=55,
            task_type_code="parser_add_channel",
            status="done",
            attempt_count=1,
            postpone_count=0,
            last_error="insufficient_resource:42:get_entity",
            last_error_code="insufficient_resource",
            payload={
                "parser_id": "pid",
                "channel_ref": "@chan",
                "action_id": "bulk-action",
            },
            run_after=None,
            started_at=datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 6, 23, 10, 1, tzinfo=timezone.utc),
            last_error_at=None,
        )
        client = self._make_client()

        resp = client.get("/discovery-api/parser/queue/tasks/55")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["payload"]["parser_id"], "pid")
        self.assertEqual(body["payload"]["channel_ref"], "@chan")
        self.assertEqual(body["payload"]["action_id"], "bulk-action")
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["attempt_count"], 1)
        self.assertEqual(body["last_error"], "insufficient_resource:42:get_entity")
        self.assertEqual(body["last_error_code"], "insufficient_resource")

    @patch("discovery_api.parser_router.get_task_snapshot", new_callable=AsyncMock)
    def test_get_task_404(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = None
        client = self._make_client()

        resp = client.get("/discovery-api/parser/queue/tasks/999999")

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"], "Задача не найдена")


if __name__ == "__main__":
    unittest.main()
