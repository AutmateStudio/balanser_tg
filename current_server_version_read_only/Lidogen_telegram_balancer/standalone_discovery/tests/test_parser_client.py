"""Юнит-тесты Parser_client."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _run(coro):
    return asyncio.run(coro)


class ParserClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        await sr.release_all()
        sr.reset_for_tests()

    async def test_add_channel_expands_allowed_chat_ids(self) -> None:
        from discovery_api.session_registry import Parser_client

        pc = Parser_client("/sess/a")
        with patch.object(
            pc, "get_client", new_callable=AsyncMock, return_value=MagicMock()
        ), patch(
            "discovery_api.parser_functions.resolve_channel_to_chat_id",
            new_callable=AsyncMock,
            return_value=(-100555, None),
        ), patch.object(pc, "start", new_callable=AsyncMock):
            chat_id, err = await pc.add_channel(
                "@chan", webhook_url="http://hook.example/h"
            )

        self.assertEqual(chat_id, -100555)
        self.assertIsNone(err)
        self.assertIn(-100555, pc.allowed_chat_ids)
        self.assertIn("@chan", pc.channels)

    async def test_start_is_idempotent(self) -> None:
        from discovery_api.session_registry import Parser_client

        pc = Parser_client("/sess/b")

        async def _fake_listener(**kwargs):
            await asyncio.sleep(3600)

        with patch(
            "discovery_api.session_registry.get_or_create_client",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ), patch(
            "discovery_api.parser_functions.run_session_listener",
            side_effect=_fake_listener,
        ):
            await pc.start("http://hook")
            task1 = pc._supervisor_task
            await pc.start("http://hook")
            task2 = pc._supervisor_task

        self.assertIs(task1, task2)
        await pc.stop()

    async def test_remove_channel(self) -> None:
        from discovery_api.session_registry import Parser_client

        pc = Parser_client("/sess/c")
        pc.channels = ["@x"]
        pc.allowed_chat_ids = {-1001}
        pc.ref_to_chat_id = {"@x": -1001}

        with patch(
            "discovery_api.parser_functions.resolve_channel_to_chat_id",
            new_callable=AsyncMock,
            return_value=(-1001, None),
        ):
            ok = await pc.remove_channel("@x")

        self.assertTrue(ok)
        self.assertNotIn(-1001, pc.allowed_chat_ids)
        self.assertNotIn("@x", pc.channels)


if __name__ == "__main__":
    unittest.main()
