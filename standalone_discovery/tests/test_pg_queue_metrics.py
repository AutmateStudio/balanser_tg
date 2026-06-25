"""G3 — тесты GET /queue/metrics."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _sample_metrics_response():
    from discovery_api.queue.metrics import (
        AccountsMetricsResponse,
        AlertsPreviewResponse,
        MetricsResponse,
        PerOpUsageResponse,
        QueueMetricsResponse,
        AccountResourceResponse,
    )

    return MetricsResponse(
        queue=QueueMetricsResponse(
            total=42,
            by_status={"queued": 10, "in_progress": 2},
            by_type={"parser_add_channel": {"queued": 10}},
            oldest_queued_age_seconds=120,
            stuck_count=0,
            done_last_5_min=10,
        ),
        accounts=AccountsMetricsResponse(
            active=5,
            in_cooldown=1,
            without_resource=2,
            per_op=[
                PerOpUsageResponse(
                    account_id=1,
                    session_name="acc1",
                    account_status="active",
                    op_type_id=10,
                    op_code="get_entity",
                    effective_rph=6,
                    used_last_hour=2,
                    available_resource=4,
                    available_resource_percent=66.67,
                )
            ],
            worst_by_account=[
                AccountResourceResponse(
                    account_id=1,
                    session_name="acc1",
                    account_status="active",
                    worst_available_percent=66.67,
                    any_op_exhausted=False,
                    exhausted_ops_count=0,
                )
            ],
        ),
        alerts_preview=AlertsPreviewResponse(high_postpone_count=3),
        generated_at=datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc).isoformat(),
    )


class PgQueueMetricsApiTests(unittest.TestCase):
    def _make_client(self) -> TestClient:
        from discovery_api.parser_router import parser_router

        app = FastAPI()
        app.include_router(parser_router)
        return TestClient(app)

    @patch("discovery_api.parser_router.get_queue_metrics", new_callable=AsyncMock)
    def test_get_metrics_ok(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = _sample_metrics_response()
        client = self._make_client()

        resp = client.get("/discovery-api/parser/queue/metrics")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("queue", body)
        self.assertIn("accounts", body)
        self.assertIn("alerts_preview", body)
        self.assertIn("generated_at", body)
        self.assertEqual(body["queue"]["total"], 42)
        self.assertEqual(body["queue"]["oldest_queued_age_seconds"], 120)
        self.assertEqual(body["accounts"]["active"], 5)
        self.assertEqual(body["alerts_preview"]["high_postpone_count"], 3)
        mock_get.assert_awaited_once()

    @patch("discovery_api.parser_router.get_queue_metrics", new_callable=AsyncMock)
    def test_get_metrics_serializes_per_op_and_worst(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = _sample_metrics_response()
        client = self._make_client()

        resp = client.get("/discovery-api/parser/queue/metrics")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        per_op = body["accounts"]["per_op"][0]
        self.assertEqual(per_op["op_code"], "get_entity")
        self.assertEqual(per_op["effective_rph"], 6)
        worst = body["accounts"]["worst_by_account"][0]
        self.assertEqual(worst["worst_available_percent"], 66.67)
        self.assertFalse(worst["any_op_exhausted"])

    @patch("discovery_api.queue.metrics.get_use_pg_queue", return_value=False)
    def test_get_metrics_503_when_pg_disabled(self, _mock_pg: object) -> None:
        from discovery_api.queue.metrics import get_queue_metrics

        client = self._make_client()

        with patch(
            "discovery_api.parser_router.get_queue_metrics",
            side_effect=get_queue_metrics,
        ):
            resp = client.get("/discovery-api/parser/queue/metrics")

        self.assertEqual(resp.status_code, 503)
        self.assertIn("USE_PG_QUEUE", resp.json()["detail"])

    @patch("discovery_api.queue.metrics.get_use_pg_queue", return_value=True)
    @patch(
        "discovery_api.queue.metrics.fetch_metrics_snapshot",
        new_callable=AsyncMock,
    )
    def test_get_metrics_503_on_pool_error(
        self, mock_fetch: AsyncMock, _mock_pg: object
    ) -> None:
        from discovery_api.queue.metrics import get_queue_metrics

        mock_fetch.side_effect = RuntimeError("Пул не инициализирован — вызовите init_pool()")
        client = self._make_client()

        with patch(
            "discovery_api.parser_router.get_queue_metrics",
            side_effect=get_queue_metrics,
        ):
            resp = client.get("/discovery-api/parser/queue/metrics")

        self.assertEqual(resp.status_code, 503)
        self.assertIn("Пул не инициализирован", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
