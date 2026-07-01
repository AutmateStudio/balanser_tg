"""Тесты GET /queue/accounts/... и PG-fallback account-channels."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _pg_channels_response():
    from discovery_api.queue.account_channels import (
        AccountChannelItemResponse,
        AccountChannelsPgResponse,
    )

    return AccountChannelsPgResponse(
        session_name="Test2",
        account_id=3811,
        channel_count=1,
        channels=[
            AccountChannelItemResponse(
                channel_id=2369,
                channel_ref="https://t.me/stroycamoskva",
                name="Стройка",
                external_url="https://t.me/stroycamoskva",
                is_active=True,
                extra_data_collected=False,
                last_updated_at=None,
            )
        ],
    )


def _summary_response():
    from discovery_api.queue.account_channels import AccountChannelsSummaryResponse

    return AccountChannelsSummaryResponse(
        session_name="Test2",
        account_id=3811,
        assigned_channel_count=10,
        active_assigned_count=8,
        pending_collect_count=8,
        stale_update_count=8,
        queue_status="active",
        is_enabled=True,
    )


class PgQueueAccountChannelsApiTests(unittest.TestCase):
    def _make_client(self) -> TestClient:
        from discovery_api.parser_router import parser_router

        app = FastAPI()
        app.include_router(parser_router)
        return TestClient(app)

    @patch(
        "discovery_api.parser_router.get_account_channels_pg",
        new_callable=AsyncMock,
    )
    def test_queue_account_channels_ok(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = _pg_channels_response()
        client = self._make_client()

        resp = client.get("/discovery-api/parser/queue/accounts/Test2/channels")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["account_id"], 3811)
        self.assertEqual(body["source"], "pg")
        self.assertEqual(len(body["channels"]), 1)
        self.assertEqual(body["channels"][0]["channel_id"], 2369)
        mock_get.assert_awaited_once_with("Test2")

    @patch(
        "discovery_api.parser_router.get_account_channels_summary",
        new_callable=AsyncMock,
    )
    def test_queue_account_summary_ok(self, mock_summary: AsyncMock) -> None:
        mock_summary.return_value = _summary_response()
        client = self._make_client()

        resp = client.get("/discovery-api/parser/queue/accounts/Test2/summary")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["pending_collect_count"], 8)
        self.assertEqual(body["stale_update_count"], 8)
        mock_summary.assert_awaited_once_with("Test2")

    @patch("discovery_api.queue.account_channels.get_use_pg_queue", return_value=False)
    def test_queue_account_channels_503_when_pg_disabled(
        self, _mock_pg: object
    ) -> None:
        from discovery_api.queue.account_channels import get_account_channels_pg

        client = self._make_client()
        with patch(
            "discovery_api.parser_router.get_account_channels_pg",
            side_effect=get_account_channels_pg,
        ):
            resp = client.get("/discovery-api/parser/queue/accounts/Test2/channels")

        self.assertEqual(resp.status_code, 503)
        self.assertIn("USE_PG_QUEUE", resp.json()["detail"])

    @patch("discovery_api.parser_router.get_use_pg_queue", return_value=True)
    @patch(
        "discovery_api.parser_router.get_account_channels_pg",
        new_callable=AsyncMock,
    )
    def test_account_channels_pg_fallback(
        self, mock_pg: AsyncMock, _mock_flag: object
    ) -> None:
        from fastapi import HTTPException

        mock_pg.return_value = _pg_channels_response()
        client = self._make_client()

        with patch(
            "discovery_api.parser_router._find_account_job",
            side_effect=HTTPException(status_code=404, detail="not in clump"),
        ):
            resp = client.get(
                "/discovery-api/parser/account-channels",
                params={"session_name": "Test2"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["source"], "pg")
        self.assertEqual(body["account_id"], 3811)
        self.assertEqual(body["channels"], ["https://t.me/stroycamoskva"])
        mock_pg.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
