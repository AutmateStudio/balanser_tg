"""Юнит-тесты для `_is_group_entity` и сериализации `GroupDiscoveryResponse`.

Проверяем, что:
- классические `Chat` (обычные группы) считаются группами;
- `ChatForbidden` / deactivated / migrated_to отсеиваются;
- `Channel` без `megagroup`/`gigagroup` (broadcast-канал) — не группа;
- pydantic-модель `GroupDiscoveryResponse` корректно сериализуется с непустым
  полем `errors`.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from telethon.tl import types

from discovery_api.discovery import _is_group_entity
from discovery_api.router import GroupDiscoveryResponse


def _make_channel(*, megagroup=False, gigagroup=False, broadcast=False) -> MagicMock:
    ent = MagicMock(spec=types.Channel)
    ent.megagroup = megagroup
    ent.gigagroup = gigagroup
    ent.broadcast = broadcast
    return ent


def _make_chat(*, deactivated=False, migrated_to=None) -> MagicMock:
    ent = MagicMock(spec=types.Chat)
    ent.deactivated = deactivated
    ent.migrated_to = migrated_to
    return ent


class IsGroupEntityTests(unittest.TestCase):
    def test_megagroup_is_group(self) -> None:
        self.assertTrue(_is_group_entity(_make_channel(megagroup=True)))

    def test_gigagroup_is_group(self) -> None:
        self.assertTrue(_is_group_entity(_make_channel(gigagroup=True)))

    def test_broadcast_channel_is_not_group(self) -> None:
        self.assertFalse(_is_group_entity(_make_channel(broadcast=True)))

    def test_megagroup_with_broadcast_is_not_group(self) -> None:
        self.assertFalse(
            _is_group_entity(_make_channel(megagroup=True, broadcast=True))
        )

    def test_plain_chat_is_group(self) -> None:
        self.assertTrue(_is_group_entity(_make_chat()))

    def test_deactivated_chat_is_not_group(self) -> None:
        self.assertFalse(_is_group_entity(_make_chat(deactivated=True)))

    def test_migrated_chat_is_not_group(self) -> None:
        self.assertFalse(_is_group_entity(_make_chat(migrated_to=object())))

    def test_chat_forbidden_is_not_group(self) -> None:
        ent = MagicMock(spec=types.ChatForbidden)
        self.assertFalse(_is_group_entity(ent))

    def test_unknown_entity_is_not_group(self) -> None:
        self.assertFalse(_is_group_entity(object()))


class GroupDiscoveryResponseTests(unittest.TestCase):
    def test_errors_default_empty(self) -> None:
        resp = GroupDiscoveryResponse(
            query="q", seeds=[], total=0, depth_stats={}, groups=[]
        )
        self.assertEqual(resp.errors, [])

    def test_errors_serialized(self) -> None:
        resp = GroupDiscoveryResponse(
            query="q",
            seeds=[],
            total=0,
            depth_stats={},
            groups=[],
            errors=["contacts.Search('foo'): FloodWait 30"],
        )
        dumped = resp.model_dump()
        self.assertEqual(
            dumped["errors"], ["contacts.Search('foo'): FloodWait 30"]
        )


if __name__ == "__main__":
    unittest.main()
