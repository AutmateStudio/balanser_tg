"""Тесты add-channel-by-link с resolve цели прослушивания."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from discovery_api.chat_resolve import ChannelHasNoDiscussionError, ListenTarget
from telethon.errors import FloodWaitError


class AddChannelListenResolveTests(unittest.TestCase):
    def test_add_channel_by_link_no_discussion_returns_400(self) -> None:
        from fastapi import Depends, FastAPI

        from discovery_api.api_key_auth import require_api_key
        from discovery_api.router import router

        app = FastAPI()
        app.include_router(router, dependencies=[Depends(require_api_key)])

        fake = MagicMock()
        fake.is_connected.return_value = True

        with patch.dict(os.environ, {"API_KEY": "test-key"}, clear=False), patch(
            "discovery_api.router.get_or_create_client", new_callable=AsyncMock, return_value=fake
        ), patch(
            "discovery_api.router.add_channel_via_link",
            new_callable=AsyncMock,
            side_effect=ChannelHasNoDiscussionError("нет обсуждений"),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            r = client.post(
                "/discovery-api/add-channel-by-link",
                headers={"X-API-Key": "test-key", "Content-Type": "application/json"},
                json={"session_name": "/app/sessions/x", "link": "https://t.me/nodiscuss"},
            )

        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("обсуждений", r.json()["detail"])

    def test_resolve_channel_to_chat_id_returns_listen_peer_id(self) -> None:
        from discovery_api.parser_functions import resolve_channel_to_chat_id

        target = ListenTarget(
            source_entity=MagicMock(),
            listen_entity=MagicMock(),
            source_peer_id=-100111,
            listen_peer_id=-100222,
            entity_kind="channel",
            listen_mode="discussion",
            linked_chat_id=222,
            title="News",
            username="news",
        )
        client = MagicMock()
        client.is_connected.return_value = True

        with patch(
            "discovery_api.parser_functions.resolve_listen_target",
            new_callable=AsyncMock,
            return_value=target,
        ):
            chat_id, err = __import__("asyncio").run(
                resolve_channel_to_chat_id(client, "https://t.me/news")
            )

        self.assertIsNone(err)
        self.assertEqual(chat_id, -100222)

    def test_resolve_channel_to_chat_id_join_flood_wait_returns_flood_string(self) -> None:
        from discovery_api.parser_functions import resolve_channel_to_chat_id

        client = MagicMock()
        client.is_connected.return_value = True
        unique_ref = f"https://t.me/flood_join_test_{id(self)}"

        with patch(
            "discovery_api.parser_functions.get_cached_chat_ids",
            return_value={},
        ), patch(
            "discovery_api.parser_functions.resolve_listen_target",
            new_callable=AsyncMock,
            side_effect=FloodWaitError(request=None, capture=270),
        ):
            chat_id, err = __import__("asyncio").run(
                resolve_channel_to_chat_id(client, unique_ref)
            )

        self.assertIsNone(chat_id)
        self.assertIn("FloodWait 270s", err or "")


if __name__ == "__main__":
    unittest.main()
