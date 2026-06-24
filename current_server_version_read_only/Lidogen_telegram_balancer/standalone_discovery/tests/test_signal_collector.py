"""Тесты сборщика сигналов с мок-клиентом Telethon."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from discovery_api.score_channel import signal_collector


def _fake_message(*, hours_ago: float = 0, views: int = 1000) -> MagicMock:
    m = MagicMock()
    m.action = None
    dt = datetime.now(timezone.utc)
    m.date = dt
    m.views = views
    m.forwards = 1
    m.reactions = None
    m.replies = None
    return m


class CollectSignalsMockTests(unittest.IsolatedAsyncioTestCase):
    async def test_collect_uses_full_info_when_passed(self) -> None:
        ch = SimpleNamespace(
            id=123,
            access_hash=456,
            title="Chan",
            username="chanuser",
            participants_count=100,
            megagroup=False,
            broadcast=True,
            scam=False,
            fake=False,
            restricted=False,
            date=None,
        )
        full_chat = SimpleNamespace(
            about="about text",
            participants_count=999,
            online_count=10,
            linked_chat_id=None,
            slowmode_seconds=0,
        )
        full_info = SimpleNamespace(full_chat=full_chat, chats=[ch])

        msgs = [_fake_message() for _ in range(3)]

        async def _agen():
            for m in msgs:
                yield m

        client = MagicMock()
        client.iter_messages = MagicMock(return_value=_agen())

        sig = await signal_collector.collect_lidgen_signals(client, ch, full_info=full_info)
        self.assertEqual(sig.get("participants_count"), 999)
        self.assertEqual(len(sig.get("posts") or []), 3)


if __name__ == "__main__":
    unittest.main()
