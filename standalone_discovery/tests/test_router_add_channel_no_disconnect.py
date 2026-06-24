"""Проверка, что `/add-channel-by-link` не вызывает `disconnect()` у общего клиента."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient


class AddChannelNoDisconnectTests(unittest.TestCase):
    def test_add_channel_by_link_does_not_disconnect_shared_client(self) -> None:
        from discovery_api.main import app

        fake = MagicMock()
        fake.is_connected.return_value = True
        fake.disconnect = AsyncMock()

        payload = {
            "peer_id": 1,
            "title": "T",
            "username": "u",
            "participants_count": 1,
            "depth": 0,
            "source": "search",
            "recommended_by": None,
            "score": 0,
            "score_breakdown": {},
            "score_signals": {},
            "score_hard_flags": {},
        }

        with patch.dict(os.environ, {"API_KEY": "test-key"}, clear=False), patch(
            "discovery_api.router.get_or_create_client", new_callable=AsyncMock, return_value=fake
        ), patch(
            "discovery_api.router.add_channel_via_link", new_callable=AsyncMock, return_value=payload
        ):
            client = TestClient(app, raise_server_exceptions=True)
            r = client.post(
                "/discovery-api/add-channel-by-link",
                headers={"X-API-Key": "test-key", "Content-Type": "application/json"},
                json={"session_name": "/app/sessions/x", "link": "https://t.me/test"},
            )

        self.assertEqual(r.status_code, 200, r.text)
        fake.disconnect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
