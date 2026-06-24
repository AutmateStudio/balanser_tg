"""Тесты для резолва информации об отправителе сообщения в парсере.

Покрываем:
- `_extract_sender_info` для User / Channel / Chat / None;
- кеширование `resolve_sender_info` по `sender_id` и работу с TTL;
- интеграцию с handler-ом: `sender` появляется в envelope парсера.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from telethon.tl import types as tl_types

from discovery_api import parser_functions as pf


def _run(coro):
    return asyncio.run(coro)


def _make_user(**kwargs) -> MagicMock:
    user = MagicMock(spec=tl_types.User)
    user.id = kwargs.get("id", 111)
    user.access_hash = kwargs.get("access_hash", 222)
    user.username = kwargs.get("username", "alice")
    user.first_name = kwargs.get("first_name", "Alice")
    user.last_name = kwargs.get("last_name", None)
    user.phone = kwargs.get("phone", None)
    user.bot = kwargs.get("bot", False)
    user.premium = kwargs.get("premium", True)
    user.verified = kwargs.get("verified", False)
    user.scam = kwargs.get("scam", False)
    user.fake = kwargs.get("fake", False)
    user.restricted = kwargs.get("restricted", False)
    user.restriction_reason = kwargs.get("restriction_reason", None)
    user.deleted = kwargs.get("deleted", False)
    user.lang_code = kwargs.get("lang_code", "ru")
    user.is_self = kwargs.get("is_self", False)
    user.contact = kwargs.get("contact", False)
    user.mutual_contact = kwargs.get("mutual_contact", False)
    return user


def _make_channel(**kwargs) -> MagicMock:
    ch = MagicMock(spec=tl_types.Channel)
    ch.id = kwargs.get("id", 12345)
    ch.access_hash = kwargs.get("access_hash", 7)
    ch.title = kwargs.get("title", "Some channel")
    ch.username = kwargs.get("username", None)
    ch.participants_count = kwargs.get("participants_count", 100)
    ch.broadcast = kwargs.get("broadcast", True)
    ch.megagroup = kwargs.get("megagroup", False)
    ch.gigagroup = kwargs.get("gigagroup", False)
    ch.forum = kwargs.get("forum", False)
    ch.verified = kwargs.get("verified", False)
    ch.scam = kwargs.get("scam", False)
    ch.fake = kwargs.get("fake", False)
    ch.restricted = kwargs.get("restricted", False)
    ch.restriction_reason = kwargs.get("restriction_reason", None)
    return ch


class ExtractSenderInfoTests(unittest.TestCase):
    def test_user_basic_fields(self) -> None:
        user = _make_user(first_name="Боб", username="bob", premium=True)
        info = pf._extract_sender_info(user)
        self.assertEqual(info["type"], "user")
        self.assertEqual(info["first_name"], "Боб")
        self.assertEqual(info["username"], "bob")
        self.assertTrue(info["premium"])
        self.assertFalse(info["bot"])

    def test_bot_user_has_type_bot(self) -> None:
        bot = _make_user(bot=True, username="my_bot")
        info = pf._extract_sender_info(bot)
        self.assertEqual(info["type"], "bot")
        self.assertTrue(info["bot"])

    def test_channel(self) -> None:
        ch = _make_channel(title="Канал", broadcast=True, megagroup=False)
        info = pf._extract_sender_info(ch)
        self.assertEqual(info["type"], "channel")
        self.assertEqual(info["title"], "Канал")
        self.assertTrue(info["broadcast"])
        self.assertFalse(info["megagroup"])

    def test_none_returns_unknown(self) -> None:
        info = pf._extract_sender_info(None)
        self.assertEqual(info, {"type": "unknown"})


class ResolveSenderInfoTests(unittest.TestCase):
    def setUp(self) -> None:
        pf.reset_sender_cache()
        os.environ["PARSER_RESOLVE_FULL_USER"] = "0"

    def tearDown(self) -> None:
        pf.reset_sender_cache()
        os.environ.pop("PARSER_RESOLVE_FULL_USER", None)

    def test_returns_unknown_for_missing_sender_id(self) -> None:
        event = SimpleNamespace(sender_id=None)
        info = _run(pf.resolve_sender_info(event, client=None))
        self.assertEqual(info, {"id": None, "type": "unknown"})

    def test_uses_cache_on_second_call(self) -> None:
        user = _make_user(id=42, username="alice")
        get_sender = AsyncMock(return_value=user)
        event = SimpleNamespace(sender_id=42, get_sender=get_sender)

        info1 = _run(pf.resolve_sender_info(event, client=None))
        info2 = _run(pf.resolve_sender_info(event, client=None))

        self.assertEqual(info1["username"], "alice")
        self.assertEqual(info2["username"], "alice")
        # Второй вызов должен быть из кеша — get_sender вызвался только 1 раз.
        self.assertEqual(get_sender.await_count, 1)

    def test_cache_expires(self) -> None:
        user = _make_user(id=42, username="alice")
        get_sender = AsyncMock(return_value=user)
        event = SimpleNamespace(sender_id=42, get_sender=get_sender)

        _run(pf.resolve_sender_info(event, client=None, ttl=0.0))
        _run(pf.resolve_sender_info(event, client=None, ttl=0.0))
        # При нулевом TTL кеш всегда «истёкший», поэтому вызовов будет два.
        self.assertEqual(get_sender.await_count, 2)

    def test_error_in_get_sender_is_swallowed(self) -> None:
        get_sender = AsyncMock(side_effect=RuntimeError("no network"))
        event = SimpleNamespace(sender_id=42, get_sender=get_sender)

        info = _run(pf.resolve_sender_info(event, client=None))
        self.assertEqual(info["id"], 42)
        self.assertEqual(info["type"], "unknown")
        self.assertIn("resolve_error", info)


class HandlerIncludesSenderTests(unittest.TestCase):
    def setUp(self) -> None:
        pf.reset_sender_cache()
        os.environ["PARSER_RESOLVE_FULL_USER"] = "0"

    def tearDown(self) -> None:
        pf.reset_sender_cache()
        os.environ.pop("PARSER_RESOLVE_FULL_USER", None)

    def test_envelope_includes_sender(self) -> None:
        async def _run_handler() -> dict:
            q: asyncio.Queue = asyncio.Queue(maxsize=10)
            allowed = {-1001}
            handler = pf._make_new_message_handler(
                allowed_chat_ids=allowed,
                queue=q,
                webhook_url="http://hook",
                client=None,
            )

            user = _make_user(id=777, username="charlie")
            ev = SimpleNamespace(
                chat_id=-1001,
                sender_id=777,
                is_private=False,
                is_group=False,
                is_channel=True,
                get_sender=AsyncMock(return_value=user),
                message=SimpleNamespace(
                    id=1,
                    message="hi",
                    raw_text="hi",
                    date=None,
                    reply_to=None,
                ),
            )

            await handler(ev)
            return q.get_nowait()

        item = asyncio.run(_run_handler())
        self.assertEqual(item["webhook_url"], "http://hook")
        msg = item["telegram_message"]
        self.assertIn("sender", msg)
        self.assertEqual(msg["sender"]["username"], "charlie")
        self.assertEqual(msg["sender"]["type"], "user")

    def test_handler_still_works_when_sender_resolve_fails(self) -> None:
        async def _run_handler() -> dict:
            q: asyncio.Queue = asyncio.Queue(maxsize=10)
            allowed = {-1001}
            handler = pf._make_new_message_handler(
                allowed_chat_ids=allowed,
                queue=q,
                webhook_url="http://hook",
                client=None,
            )

            ev = SimpleNamespace(
                chat_id=-1001,
                sender_id=777,
                is_private=False,
                is_group=False,
                is_channel=True,
                get_sender=AsyncMock(side_effect=RuntimeError("boom")),
                message=SimpleNamespace(
                    id=1,
                    message="hi",
                    raw_text="hi",
                    date=None,
                    reply_to=None,
                ),
            )

            await handler(ev)
            return q.get_nowait()

        item = asyncio.run(_run_handler())
        msg = item["telegram_message"]
        self.assertEqual(msg["chat_id"], -1001)
        self.assertIn("sender", msg)
        self.assertIn("resolve_error", msg["sender"])


if __name__ == "__main__":
    unittest.main()
