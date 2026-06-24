"""Тесты дополнения /discover поиском по тексту сообщений (messages.SearchGlobal).

Проверяем, что:
- `_search_channels_global` оставляет только broadcast-каналы и гасит исключения;
- `_discover` подмешивает результаты глобального поиска, дедуплицируя их с
  contacts.Search (первый источник побеждает);
- при `include_global_search=False` глобальный поиск вообще не вызывается.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from telethon.tl import types

from discovery_api import discovery
from discovery_api.discovery import (
    DiscoveredChannel,
    _is_discoverable_entity,
    _search_channels,
    _search_channels_global,
)


def _run(coro):
    return asyncio.run(coro)


def _make_channel(*, broadcast=False, megagroup=False, channel_id=1, title="T"):
    ch = MagicMock(spec=types.Channel)
    ch.broadcast = broadcast
    ch.megagroup = megagroup
    ch.gigagroup = False
    ch.id = channel_id
    ch.title = title
    ch.username = "u"
    return ch


def _make_chat(*, chat_id=100, deactivated=False, migrated_to=None, title="C"):
    ch = MagicMock(spec=types.Chat)
    ch.id = chat_id
    ch.deactivated = deactivated
    ch.migrated_to = migrated_to
    ch.title = title
    return ch


async def _fake_score(client, entity, *, query, depth, source, recommended_by, sem):
    return DiscoveredChannel(
        peer_id=entity.id,
        title=entity.title,
        username=None,
        participants_count=100,
        depth=depth,
        source=source,
        recommended_by=recommended_by,
        score_total=50,
    )


class SearchChannelsGlobalTests(unittest.TestCase):
    def test_filters_broadcast_only(self) -> None:
        res = MagicMock()
        res.chats = [
            _make_channel(broadcast=True, channel_id=1),
            _make_channel(megagroup=True, channel_id=2),
            MagicMock(spec=types.Chat),
        ]
        client = AsyncMock(return_value=res)
        out = _run(_search_channels_global(client, "ищу подрядчика", 10))
        self.assertEqual([c.id for c in out], [1])

    def test_returns_empty_on_exception(self) -> None:
        client = AsyncMock(side_effect=RuntimeError("boom"))
        out = _run(_search_channels_global(client, "q", 10))
        self.assertEqual(out, [])


class DiscoverGlobalIntegrationTests(unittest.TestCase):
    def test_includes_and_dedupes_global(self) -> None:
        contacts = [_make_channel(broadcast=True, channel_id=1), _make_channel(broadcast=True, channel_id=2)]
        glob = [_make_channel(broadcast=True, channel_id=2), _make_channel(broadcast=True, channel_id=3)]
        client = AsyncMock()
        with patch.object(discovery, "_search_channels", AsyncMock(return_value=contacts)), patch.object(
            discovery, "_search_channels_global", AsyncMock(return_value=glob)
        ), patch.object(discovery, "_score_discovered_channel_lidgen", _fake_score):
            result = _run(discovery._discover(client, "q", 10, 0, 0.0, include_global_search=True))
        sources = {c.peer_id: c.source for c in result.channels}
        self.assertEqual(sorted(sources), [1, 2, 3])
        self.assertEqual(sources[1], "search")
        self.assertEqual(sources[2], "search")  # дубль id=2 из global отброшен, остаётся первый источник
        self.assertEqual(sources[3], "global_search")

    def test_global_disabled(self) -> None:
        contacts = [_make_channel(broadcast=True, channel_id=1)]
        global_mock = AsyncMock(return_value=[_make_channel(broadcast=True, channel_id=9)])
        client = AsyncMock()
        with patch.object(discovery, "_search_channels", AsyncMock(return_value=contacts)), patch.object(
            discovery, "_search_channels_global", global_mock
        ), patch.object(discovery, "_score_discovered_channel_lidgen", _fake_score):
            result = _run(discovery._discover(client, "q", 10, 0, 0.0, include_global_search=False))
        global_mock.assert_not_called()
        self.assertEqual([c.peer_id for c in result.channels], [1])


class IsDiscoverableEntityTests(unittest.TestCase):
    def test_broadcast_always_discoverable(self) -> None:
        ch = _make_channel(broadcast=True)
        self.assertTrue(_is_discoverable_entity(ch, include_groups=False))
        self.assertTrue(_is_discoverable_entity(ch, include_groups=True))

    def test_megagroup_only_when_groups_enabled(self) -> None:
        mg = _make_channel(megagroup=True)
        self.assertFalse(_is_discoverable_entity(mg, include_groups=False))
        self.assertTrue(_is_discoverable_entity(mg, include_groups=True))

    def test_classic_chat_only_when_groups_enabled(self) -> None:
        chat = _make_chat()
        self.assertFalse(_is_discoverable_entity(chat, include_groups=False))
        self.assertTrue(_is_discoverable_entity(chat, include_groups=True))


class SearchChannelsIncludeGroupsTests(unittest.TestCase):
    def _client(self):
        res = MagicMock()
        res.chats = [
            _make_channel(broadcast=True, channel_id=1),
            _make_channel(megagroup=True, channel_id=2),
            _make_chat(chat_id=3),
        ]
        return AsyncMock(return_value=res)

    def test_default_broadcast_only(self) -> None:
        out = _run(_search_channels(self._client(), "ягоды", 10))
        self.assertEqual([c.id for c in out], [1])

    def test_include_groups_returns_channels_and_groups(self) -> None:
        out = _run(_search_channels(self._client(), "ягоды", 10, include_groups=True))
        self.assertEqual(sorted(c.id for c in out), [1, 2, 3])

    def test_global_include_groups(self) -> None:
        out = _run(_search_channels_global(self._client(), "ягоды", 10, include_groups=True))
        self.assertEqual(sorted(c.id for c in out), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
