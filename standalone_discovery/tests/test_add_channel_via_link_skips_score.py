"""Ручное добавление по ссылке не ждёт сбор сигналов и score."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from discovery_api.add_channel_via_link_or_name import add_channel_via_link


class AddChannelViaLinkSkipsScoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_metadata_without_scoring(self) -> None:
        chat = SimpleNamespace(title="Тест", username="test_channel", participants_count=10)
        full_info = SimpleNamespace(
            chats=[chat],
            full_chat=SimpleNamespace(
                about="описание",
                participants_count=10,
                online_count=None,
                admins_count=None,
                kicked_count=None,
                banned_count=None,
                linked_chat_id=None,
                slowmode_seconds=None,
                pinned_msg_id=None,
                read_inbox_max_id=None,
            ),
        )
        target = SimpleNamespace(
            source_entity=SimpleNamespace(),
            full_info=full_info,
            source_peer_id=-100123,
            listen_peer_id=-100123,
            entity_kind="channel",
            listen_mode="broadcast",
            source_joined=True,
            listen_joined=True,
            has_listen_access=True,
            access_note=None,
            title="Тест",
            username="test_channel",
            linked_chat_id=None,
        )
        score = AsyncMock(side_effect=AssertionError("score не должен вызываться"))

        with (
            patch(
                "discovery_api.add_channel_via_link_or_name.resolve_listen_target",
                new=AsyncMock(return_value=target),
            ),
            patch(
                "discovery_api.add_channel_via_link_or_name.extract_channel_meta",
                return_value={},
            ),
            patch(
                "discovery_api.add_channel_via_link_or_name.calculate_score_over_added_channel",
                new=score,
            ),
        ):
            payload = await add_channel_via_link(AsyncMock(), "https://t.me/test_channel")

        score.assert_not_called()
        self.assertIsNone(payload["score"])
        self.assertEqual(payload["title"], "Тест")
        self.assertEqual(payload["about"], "описание")
        self.assertEqual(payload["username"], "test_channel")
