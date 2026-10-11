"""Webhook парсера несёт mapping source/listen, а не только chat_id события."""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from discovery_api.parser_functions import _telegram_message_payload


class ParserIdentityPayloadTests(unittest.TestCase):
    def test_discussion_event_keeps_source_channel_id(self) -> None:
        msg = SimpleNamespace(id=15, message="нужен обмен", raw_text="нужен обмен", date=None, reply_to=None)
        event = SimpleNamespace(
            sender_id=7,
            chat_id=-100222,
            is_private=False,
            is_group=True,
            is_channel=False,
        )
        identity = {
            "source_peer_id": -100111,
            "listen_peer_id": -100222,
            "linked_chat_id": 222,
            "linked_peer_id": -100222,
            "listen_mode": "discussion",
            "entity_kind": "channel",
        }
        payload = _telegram_message_payload(msg, event, identity=identity)
        message = payload["telegram_message"]
        self.assertEqual(message["chat_id"], -100222)
        self.assertEqual(message["text"], "нужен обмен")
        self.assertEqual(message["source_peer_id"], -100111)
        self.assertEqual(message["listen_mode"], "discussion")
        self.assertEqual(message["linked_peer_id"], -100222)


if __name__ == "__main__":
    unittest.main()
