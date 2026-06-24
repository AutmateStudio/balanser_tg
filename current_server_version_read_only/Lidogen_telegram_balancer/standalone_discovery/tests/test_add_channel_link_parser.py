"""Юнит-тесты парсера ссылок для эндпойнта `/discovery-api/add-channel-by-link`."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from discovery_api.add_channel_via_link_or_name import (
    _extract_channel_name_from_link,
)


class ExtractChannelNameFromLinkTests(unittest.TestCase):
    def test_plain_username(self) -> None:
        self.assertEqual(_extract_channel_name_from_link("durov"), "durov")
        self.assertEqual(_extract_channel_name_from_link("@durov"), "durov")

    def test_full_link(self) -> None:
        self.assertEqual(
            _extract_channel_name_from_link("https://t.me/durov"), "durov"
        )
        self.assertEqual(
            _extract_channel_name_from_link("t.me/durov"), "durov"
        )

    def test_message_link_returns_only_username(self) -> None:
        self.assertEqual(
            _extract_channel_name_from_link("https://t.me/sutki_chat/716983"),
            "sutki_chat",
        )
        self.assertEqual(
            _extract_channel_name_from_link("https://t.me/balichat/3452435/"),
            "balichat",
        )

    def test_query_and_fragment_stripped(self) -> None:
        self.assertEqual(
            _extract_channel_name_from_link("https://t.me/durov?single"),
            "durov",
        )
        self.assertEqual(
            _extract_channel_name_from_link("https://t.me/durov/123?single"),
            "durov",
        )

    def test_private_channel_link_returns_int_chat_id(self) -> None:
        self.assertEqual(
            _extract_channel_name_from_link("https://t.me/c/2086716036/123"),
            -1002086716036,
        )
        self.assertEqual(
            _extract_channel_name_from_link("https://t.me/c/2086716036"),
            -1002086716036,
        )

    def test_invite_link_preserved(self) -> None:
        self.assertEqual(
            _extract_channel_name_from_link("https://t.me/joinchat/AAAhash"),
            "joinchat/AAAhash",
        )
        self.assertEqual(
            _extract_channel_name_from_link("https://t.me/+AAAhash"),
            "+AAAhash",
        )

    def test_empty(self) -> None:
        self.assertEqual(_extract_channel_name_from_link(""), "")
        self.assertEqual(_extract_channel_name_from_link("   "), "")


if __name__ == "__main__":
    unittest.main()
