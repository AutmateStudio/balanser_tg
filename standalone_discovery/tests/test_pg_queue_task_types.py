"""Тесты GET/PATCH /queue/task-types (task-types RPH API)."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _sample_list_item():
    from discovery_api.queue.task_types import TaskTypeListItemResponse

    return TaskTypeListItemResponse(
        code="parser_add_channel",
        name="Добавление канала в парсер",
        description="…",
        rph_limit_effective=223,
        rph_limit_default=223,
        primary_op_code="channels.JoinChannel",
        rph_auto_reduced=False,
        rph_reduced_at=None,
    )


def _sample_detail():
    from discovery_api.queue.task_types import TaskTypeDetailResponse

    return TaskTypeDetailResponse(
        code="parser_add_channel",
        name="Добавление канала в парсер",
        description="…",
        rph_limit_effective=223,
        rph_limit_default=223,
        primary_op_code="channels.JoinChannel",
        rph_auto_reduced=False,
        rph_reduced_at=None,
        is_enabled=True,
        default_priority=500,
        min_available_resource_percent=20,
        target_queue_size=None,
        max_attempts=5,
        retry_delay_seconds=10,
        max_postpone_count=100,
        task_timeout_seconds=300,
    )


class PgQueueTaskTypesApiTests(unittest.TestCase):
    def _make_client(self) -> TestClient:
        from discovery_api.parser_router import parser_router

        app = FastAPI()
        app.include_router(parser_router)
        return TestClient(app)

    @patch("discovery_api.parser_router.list_task_types", new_callable=AsyncMock)
    def test_get_task_types_list_ok(self, mock_list: AsyncMock) -> None:
        mock_list.return_value = [_sample_list_item()]
        client = self._make_client()

        resp = client.get("/discovery-api/parser/queue/task-types")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsInstance(body, list)
        self.assertEqual(body[0]["code"], "parser_add_channel")
        self.assertEqual(body[0]["primary_op_code"], "channels.JoinChannel")
        mock_list.assert_awaited_once()

    @patch("discovery_api.parser_router.get_task_type", new_callable=AsyncMock)
    def test_get_task_type_detail_ok(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = _sample_detail()
        client = self._make_client()

        resp = client.get("/discovery-api/parser/queue/task-types/parser_add_channel")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], "parser_add_channel")
        self.assertTrue(body["is_enabled"])
        mock_get.assert_awaited_once()

    @patch("discovery_api.parser_router.patch_task_type", new_callable=AsyncMock)
    def test_patch_task_type_ok(self, mock_patch: AsyncMock) -> None:
        updated = _sample_detail()
        updated = updated.model_copy(update={"rph_limit_effective": 230})
        mock_patch.return_value = updated
        client = self._make_client()

        resp = client.patch(
            "/discovery-api/parser/queue/task-types/parser_add_channel",
            json={"rph_limit": 230},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["rph_limit_effective"], 230)
        mock_patch.assert_awaited_once()

    @patch("discovery_api.queue.task_types.get_use_pg_queue", return_value=False)
    def test_get_task_types_503_when_pg_disabled(self, _mock_pg: object) -> None:
        from discovery_api.queue.task_types import list_task_types

        client = self._make_client()

        with patch(
            "discovery_api.parser_router.list_task_types",
            side_effect=list_task_types,
        ):
            resp = client.get("/discovery-api/parser/queue/task-types")

        self.assertEqual(resp.status_code, 503)
        self.assertIn("USE_PG_QUEUE", resp.json()["detail"])

    @patch("discovery_api.parser_router.get_task_type", new_callable=AsyncMock)
    def test_get_task_type_404(self, mock_get: AsyncMock) -> None:
        from fastapi import HTTPException

        mock_get.side_effect = HTTPException(
            status_code=404,
            detail="Task type not found: unknown",
        )
        client = self._make_client()

        resp = client.get("/discovery-api/parser/queue/task-types/unknown")

        self.assertEqual(resp.status_code, 404)
        self.assertIn("not found", resp.json()["detail"])

    @patch("discovery_api.queue.task_types.get_use_pg_queue", return_value=True)
    def test_patch_empty_body_400(self, _mock_pg: object) -> None:
        client = self._make_client()

        resp = client.patch(
            "/discovery-api/parser/queue/task-types/parser_add_channel",
            json={},
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("rph_limit", resp.json()["detail"])

    @patch("discovery_api.queue.task_types.get_use_pg_queue", return_value=True)
    def test_patch_conflict_rph_and_reset_400(self, _mock_pg: object) -> None:
        client = self._make_client()

        resp = client.patch(
            "/discovery-api/parser/queue/task-types/parser_add_channel",
            json={"rph_limit": 100, "reset_rph_to_default": True},
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("reset_rph_to_default", resp.json()["detail"])

    @patch("discovery_api.queue.task_types.get_use_pg_queue", return_value=True)
    def test_patch_rph_limit_below_one_400(self, _mock_pg: object) -> None:
        client = self._make_client()

        resp = client.patch(
            "/discovery-api/parser/queue/task-types/parser_add_channel",
            json={"rph_limit": 0},
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("rph_limit", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
