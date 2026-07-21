"""Тесты resolve цели прослушивания (канал → обсуждения, группа → сам чат)."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import telethon
from telethon.errors import FloodWaitError
from telethon.tl import types

from discovery_api.chat_resolve import (
    ChannelHasNoDiscussionError,
    ChatAccessError,
    _join_channel_entity,
    classify_chat_entity,
    normalize_chat_ref,
    resolve_listen_target,
)


def _run(coro):
    return asyncio.run(coro)


def _make_channel(*, broadcast=False, megagroup=False, gigagroup=False, channel_id=1, title="T"):
    ch = MagicMock(spec=types.Channel)
    ch.broadcast = broadcast
    ch.megagroup = megagroup
    ch.gigagroup = gigagroup
    ch.id = channel_id
    ch.title = title
    ch.username = "test"
    return ch


class NormalizeChatRefTests(unittest.TestCase):
    def test_username(self) -> None:
        self.assertEqual(normalize_chat_ref("https://t.me/durov"), "durov")
        self.assertEqual(normalize_chat_ref("@durov"), "durov")

    def test_private_int(self) -> None:
        self.assertEqual(
            normalize_chat_ref("https://t.me/c/2086716036/123"),
            -1002086716036,
        )

    def test_invite(self) -> None:
        self.assertEqual(normalize_chat_ref("https://t.me/+AAA"), "+AAA")


class ClassifyChatEntityTests(unittest.TestCase):
    def test_broadcast_channel(self) -> None:
        self.assertEqual(classify_chat_entity(_make_channel(broadcast=True)), "channel")

    def test_megagroup(self) -> None:
        self.assertEqual(classify_chat_entity(_make_channel(megagroup=True)), "supergroup")

    def test_chat_group(self) -> None:
        chat = MagicMock(spec=types.Chat)
        self.assertEqual(classify_chat_entity(chat), "group")


class ResolveListenTargetTests(unittest.TestCase):
    def test_broadcast_with_discussion(self) -> None:
        channel = _make_channel(broadcast=True, channel_id=111)
        discussion = _make_channel(megagroup=True, channel_id=222, title="Discuss")

        full_chat = MagicMock()
        full_chat.linked_chat_id = 222
        full_info = MagicMock()
        full_info.full_chat = full_chat

        client = AsyncMock()
        client.get_entity = AsyncMock(side_effect=[channel, discussion])
        client.side_effect = [full_info]

        def fake_peer_id(entity):
            if entity is channel:
                return -100111
            if entity is discussion:
                return -100222
            return -1

        with patch.object(telethon.utils, "get_peer_id", side_effect=fake_peer_id), patch(
            "discovery_api.chat_resolve._join_channel_entity",
            new_callable=AsyncMock,
            return_value=(True, None),
        ), patch(
            "discovery_api.chat_resolve._check_listen_access",
            new_callable=AsyncMock,
            return_value=(True, "участник"),
        ):
            target = _run(resolve_listen_target(client, "https://t.me/news"))

        self.assertEqual(target.entity_kind, "channel")
        self.assertEqual(target.listen_mode, "discussion")
        self.assertEqual(target.linked_chat_id, 222)
        self.assertEqual(target.source_peer_id, -100111)
        self.assertEqual(target.listen_peer_id, -100222)

    def test_megagroup_listens_self(self) -> None:
        group = _make_channel(megagroup=True, channel_id=333)
        client = AsyncMock()
        client.get_entity = AsyncMock(return_value=group)

        with patch.object(telethon.utils, "get_peer_id", return_value=-100333), patch(
            "discovery_api.chat_resolve._join_channel_entity",
            new_callable=AsyncMock,
            return_value=(True, None),
        ), patch(
            "discovery_api.chat_resolve._check_listen_access",
            new_callable=AsyncMock,
            return_value=(True, "участник"),
        ):
            target = _run(resolve_listen_target(client, "mygroup"))

        self.assertEqual(target.entity_kind, "supergroup")
        self.assertEqual(target.listen_mode, "group_chat")
        self.assertEqual(target.listen_peer_id, target.source_peer_id)

    def test_channel_without_discussion_raises(self) -> None:
        channel = _make_channel(broadcast=True)
        full_chat = MagicMock()
        full_chat.linked_chat_id = None
        full_info = MagicMock()
        full_info.full_chat = full_chat

        client = AsyncMock()
        client.get_entity = AsyncMock(return_value=channel)
        client.side_effect = [None, full_info]

        with patch.object(telethon.utils, "get_peer_id", return_value=-100111):
            with self.assertRaises(ChannelHasNoDiscussionError):
                _run(resolve_listen_target(client, "https://t.me/nodiscuss"))

    def test_user_raises_value_error(self) -> None:
        user = MagicMock(spec=types.User)
        client = MagicMock()
        client.get_entity = AsyncMock(return_value=user)

        with self.assertRaises(ValueError):
            _run(resolve_listen_target(client, "https://t.me/durov"))

    def test_no_access_raises(self) -> None:
        group = _make_channel(megagroup=True, channel_id=444)
        client = AsyncMock()
        client.get_entity = AsyncMock(return_value=group)

        with patch.object(telethon.utils, "get_peer_id", return_value=-100444), patch(
            "discovery_api.chat_resolve._join_channel_entity",
            new_callable=AsyncMock,
            return_value=(False, "join_pending"),
        ), patch(
            "discovery_api.chat_resolve._check_listen_access",
            new_callable=AsyncMock,
            return_value=(False, "не участник"),
        ):
            with self.assertRaises(ChatAccessError) as ctx:
                _run(resolve_listen_target(client, "@closed_group"))
            self.assertEqual(ctx.exception.code, "join_pending")
            self.assertIn("не удалось вступить", str(ctx.exception))

    def test_join_flood_wait_propagates_not_join_pending(self) -> None:
        channel = _make_channel(broadcast=True, channel_id=111)
        discussion = _make_channel(megagroup=True, channel_id=222, title="Discuss")

        full_chat = MagicMock()
        full_chat.linked_chat_id = 222
        full_info = MagicMock()
        full_info.full_chat = full_chat

        flood = FloodWaitError(request=None, capture=270)

        client = AsyncMock()
        client.get_entity = AsyncMock(side_effect=[channel, discussion])
        client.side_effect = [full_info]

        async def join_side_effect(*args, **kwargs):
            raise flood

        with patch.object(telethon.utils, "get_peer_id", side_effect=[-100111, -100222]), patch(
            "discovery_api.chat_resolve._join_channel_entity",
            new_callable=AsyncMock,
            side_effect=join_side_effect,
        ), patch(
            "discovery_api.chat_resolve._check_listen_access",
            new_callable=AsyncMock,
            return_value=(True, "участник"),
        ):
            with self.assertRaises(FloodWaitError):
                _run(resolve_listen_target(client, "https://t.me/news"))


class JoinParticipantStaggerTests(unittest.TestCase):
    def test_stagger_after_successful_join(self) -> None:
        channel = _make_channel(megagroup=True, channel_id=555, title="G")
        client = AsyncMock()
        client.return_value = MagicMock()  # JoinChannelRequest OK

        with patch.dict(os.environ, {"JOIN_PARTICIPANT_STAGGER_SECONDS": "1"}), patch(
            "discovery_api.chat_resolve.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock:
            joined, fail = _run(
                _join_channel_entity(client, channel, raw_ref="@g", role="listen")
            )

        self.assertTrue(joined)
        self.assertIsNone(fail)
        sleep_mock.assert_awaited_once_with(1.0)

    def test_no_stagger_when_already_participant(self) -> None:
        from telethon.errors import UserAlreadyParticipantError

        channel = _make_channel(megagroup=True, channel_id=556, title="G2")
        client = AsyncMock()
        client.side_effect = UserAlreadyParticipantError(request=None)

        with patch.dict(os.environ, {"JOIN_PARTICIPANT_STAGGER_SECONDS": "1"}), patch(
            "discovery_api.chat_resolve.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock:
            joined, fail = _run(
                _join_channel_entity(client, channel, raw_ref="@g2", role="listen")
            )

        self.assertTrue(joined)
        self.assertIsNone(fail)
        sleep_mock.assert_not_called()

    def test_stagger_disabled_via_env(self) -> None:
        channel = _make_channel(megagroup=True, channel_id=557, title="G3")
        client = AsyncMock()
        client.return_value = MagicMock()

        with patch.dict(os.environ, {"JOIN_PARTICIPANT_STAGGER_SECONDS": "0"}), patch(
            "discovery_api.chat_resolve.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock:
            joined, fail = _run(
                _join_channel_entity(client, channel, raw_ref="@g3", role="listen")
            )

        self.assertTrue(joined)
        self.assertIsNone(fail)
        sleep_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
