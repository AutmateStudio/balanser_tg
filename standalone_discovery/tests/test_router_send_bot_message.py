from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient


class SendBotMessageEndpointTests(unittest.TestCase):
    def test_send_bot_message_endpoint_delegates_to_sender(self) -> None:
        from discovery_api.main import app

        sent = SimpleNamespace(
            message_id=777,
            chat=SimpleNamespace(id=123),
        )

        with patch.dict(os.environ, {"API_KEY": "test-key"}, clear=False), patch(
            "discovery_api.router.send_bot_message",
            return_value=sent,
        ) as send_mock:
            client = TestClient(app, raise_server_exceptions=True)
            response = client.post(
                "/discovery-api/bot/send-message",
                headers={"X-API-Key": "test-key", "Content-Type": "application/json"},
                json={
                    "chat_id": 123,
                    "text": "<b>Привет</b>",
                    "layout": "inline",
                    "buttons": [[{"text": "Открыть", "url": "https://example.com"}]],
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True, "message_id": 777, "chat_id": 123})
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.kwargs["chat_id"], 123)
        self.assertEqual(send_mock.call_args.kwargs["message"]["text"], "<b>Привет</b>")

    def test_send_bot_message_endpoint_returns_400_for_invalid_payload(self) -> None:
        from discovery_api.main import app

        with patch.dict(os.environ, {"API_KEY": "test-key"}, clear=False), patch(
            "discovery_api.router.send_bot_message",
            side_effect=ValueError("Нужно передать text или image_url"),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.post(
                "/discovery-api/bot/send-message",
                headers={"X-API-Key": "test-key", "Content-Type": "application/json"},
                json={"chat_id": 123},
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], "Нужно передать text или image_url")


if __name__ == "__main__":
    unittest.main()
