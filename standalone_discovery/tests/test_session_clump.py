"""Юнит-тесты SessionClump (балансировка, quota, remove по владельцу)."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _run(coro):
    return asyncio.run(coro)


class SessionClumpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        await sr.release_all()
        sr.reset_for_tests()

    async def test_min_load_distributes_channels(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(
            ["/s1", "/s2", "/s3"],
            "test-clump",
            webhook_url="http://h",
        )
        call_sessions: list[str] = []
        counter = {"n": 0}

        async def _fake_add(self, raw, *, webhook_url=None):
            counter["n"] += 1
            call_sessions.append(self.session_name)
            self.channels.append(raw)
            cid = -2000 - counter["n"]
            self.allowed_chat_ids.add(cid)
            self.ref_to_chat_id[raw] = cid
            return cid, None

        with patch(
            "discovery_api.session_registry.Parser_client.add_channel",
            _fake_add,
        ):
            for ch in ["@a", "@b", "@c"]:
                await clump.add_channel(ch)

        self.assertEqual(len(set(call_sessions)), 3)
        self.assertEqual(len(clump.assignments), 3)

    async def test_quota_defers_add_channel(self) -> None:
        from discovery_api.config import get_max_channels_per_session
        from discovery_api.session_registry import ChannelQuotaExceeded, SessionClump

        clump = SessionClump(["/s1"], "q-clump", webhook_url="http://h")
        limit = get_max_channels_per_session()
        for i in range(limit):
            clump.parser_client_list[0].channels.append(f"@c{i}")

        with patch.object(
            clump, "_pick_min_load", side_effect=ChannelQuotaExceeded("лимит")
        ):
            result = await clump.add_channel("@overflow")

        self.assertTrue(result.get("deferred"))
        self.assertIn("@overflow", clump.pending_channels)

    async def test_remove_finds_owner(self) -> None:
        from discovery_api.session_registry import Parser_client, SessionClump

        clump = SessionClump(["/s1", "/s2"], "rm-clump", webhook_url="http://h")
        pc1, pc2 = clump.parser_client_list
        pc1.channels = ["@only1"]
        pc1.allowed_chat_ids = {-1001}
        pc1.ref_to_chat_id = {"@only1": -1001}
        clump.assignments["@only1"] = pc1.session_name

        with patch.object(
            Parser_client, "remove_channel", new_callable=AsyncMock, return_value=True
        ) as mock_rm:
            ok = await clump.remove_channel("@only1")

        self.assertTrue(ok)
        mock_rm.assert_awaited_once()
        self.assertNotIn("@only1", clump.assignments)

    async def test_add_channels_batch(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "batch", webhook_url="http://h")

        async def _fake_add_channel(raw: str) -> dict:
            return {
                "channel": raw,
                "session_name": "/s1",
                "chat_id": -10099,
                "error": None,
                "already_present": False,
            }

        with patch.object(clump, "add_channel", side_effect=_fake_add_channel):
            batch = await clump.add_channels_batch(["@x", "@y"])

        self.assertEqual(batch["added"], ["@x", "@y"])
        self.assertEqual(batch["errors"], [])

    async def test_add_channel_on_session_assigns_to_given_session(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2"], "d1-clump", webhook_url="http://h")
        pc1 = clump.parser_client_list[0]

        async def _fake_add(self, raw, *, webhook_url=None):
            self.channels.append(raw)
            cid = -9001
            self.allowed_chat_ids.add(cid)
            self.ref_to_chat_id[raw] = cid
            return cid, None

        with patch(
            "discovery_api.session_registry.Parser_client.add_channel",
            _fake_add,
        ):
            result = await clump.add_channel_on_session("/s1", "@target")

        self.assertIsNone(result["error"])
        self.assertEqual(result["session_name"], "/s1")
        self.assertEqual(result["chat_id"], -9001)
        self.assertEqual(clump.assignments["@target"], "/s1")
        self.assertIn("@target", pc1.channels)

    async def test_add_channel_on_session_uses_explicit_webhook(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "d1-wh", webhook_url="http://default")
        seen_wh: list[str | None] = []

        async def _fake_add(self, raw, *, webhook_url=None):
            seen_wh.append(webhook_url)
            return -1, None

        with patch(
            "discovery_api.session_registry.Parser_client.add_channel",
            _fake_add,
        ):
            await clump.add_channel_on_session(
                "/s1", "@ch", webhook_url="http://override"
            )

        self.assertEqual(seen_wh, ["http://override"])

    async def test_add_channel_on_session_already_present_same_session(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "d1-idem", webhook_url="http://h")
        pc1 = clump.parser_client_list[0]
        pc1.channels = ["@dup"]
        pc1.ref_to_chat_id = {"@dup": -1001}
        clump.assignments["@dup"] = "/s1"

        with patch(
            "discovery_api.session_registry.Parser_client.add_channel",
            new_callable=AsyncMock,
        ) as mock_add:
            result = await clump.add_channel_on_session("/s1", "@dup")

        self.assertTrue(result.get("already_present"))
        self.assertIsNone(result["error"])
        mock_add.assert_not_awaited()

    async def test_add_channel_on_session_rejects_other_session_owner(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2"], "d1-own", webhook_url="http://h")
        pc2 = clump.parser_client_list[1]
        pc2.channels = ["@busy"]
        pc2.ref_to_chat_id = {"@busy": -2002}
        clump.assignments["@busy"] = "/s2"

        with patch(
            "discovery_api.session_registry.Parser_client.add_channel",
            new_callable=AsyncMock,
        ) as mock_add:
            result = await clump.add_channel_on_session("/s1", "@busy")

        self.assertIn("другой сессии", result["error"] or "")
        self.assertEqual(result["session_name"], "/s1")
        mock_add.assert_not_awaited()

    async def test_add_channel_on_session_unknown_session(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "d1-miss", webhook_url="http://h")
        result = await clump.add_channel_on_session("/missing", "@x")

        self.assertIn("не найдена", result["error"] or "")
        self.assertNotIn("@x", clump.assignments)

    async def test_move_channel_clears_source_and_assigns_target(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2"], "d2-move", webhook_url="http://h")
        src, dst = clump.parser_client_list
        src.channels = ["@mv"]
        src.ref_to_chat_id = {"@mv": -3001}
        src.allowed_chat_ids = {-3001}
        clump.assignments["@mv"] = "/s1"

        async def _fake_add(self, raw, *, webhook_url=None):
            self.channels.append(raw)
            cid = -3002
            self.allowed_chat_ids.add(cid)
            self.ref_to_chat_id[raw] = cid
            return cid, None

        with patch(
            "discovery_api.session_registry.Parser_client.add_channel",
            _fake_add,
        ):
            result = await clump.move_channel("@mv", "/s1", "/s2")

        self.assertTrue(result.get("moved"))
        self.assertIsNone(result["error"])
        self.assertEqual(clump.assignments["@mv"], "/s2")
        self.assertNotIn("@mv", src.channels)
        self.assertIn("@mv", dst.channels)

    async def test_move_channel_idempotent_when_already_on_target(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2"], "d2-idem", webhook_url="http://h")
        dst = clump.parser_client_list[1]
        dst.channels = ["@here"]
        dst.ref_to_chat_id = {"@here": -4001}
        clump.assignments["@here"] = "/s2"

        with patch(
            "discovery_api.session_registry.Parser_client.add_channel",
            new_callable=AsyncMock,
        ) as mock_add:
            result = await clump.move_channel("@here", "/s1", "/s2")

        self.assertTrue(result.get("already_present"))
        self.assertFalse(result.get("moved"))
        mock_add.assert_not_awaited()

    async def test_move_channel_rejects_unexpected_owner(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2", "/s3"], "d2-own", webhook_url="http://h")
        pc3 = clump.parser_client_list[2]
        pc3.channels = ["@x"]
        clump.assignments["@x"] = "/s3"

        result = await clump.move_channel("@x", "/s1", "/s2")
        self.assertIn("неожиданной", result["error"] or "")


if __name__ == "__main__":
    unittest.main()
