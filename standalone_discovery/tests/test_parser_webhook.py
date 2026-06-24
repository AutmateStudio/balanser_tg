import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import aiohttp


class _Resp:
    def __init__(self):
        self.headers = {"Content-Type": "text/plain"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self) -> str:
        return "ok"


class ParserWebhookHeaderTests(unittest.TestCase):
    def test_async_sender_sends_x_api_key_header(self) -> None:
        from discovery_api.parser_functions import AsyncSender

        async def _run() -> None:
            with patch("discovery_api.parser_functions.aiohttp.ClientSession") as session_ctor:
                session_mock = MagicMock()
                session_ctor.return_value = session_mock
                sender = AsyncSender("https://example.com/hook", api_key="out-key-1")
                self.assertTrue(session_ctor.called)
                _, kwargs = session_ctor.call_args
                self.assertIn("connector", kwargs)
                self.assertIn("timeout", kwargs)

                captured: dict = {}

                def _post(url: str, **kwargs):
                    captured["url"] = url
                    captured.update(kwargs)
                    return _Resp()

                sender.session.post.side_effect = _post

                await sender.send_message({"a": 1})
                self.assertIn("headers", captured)
                self.assertEqual(captured["headers"].get("X-API-Key"), "out-key-1")

        asyncio.run(_run())

    def test_async_sender_omits_header_when_empty(self) -> None:
        from discovery_api.parser_functions import AsyncSender

        async def _run() -> None:
            with patch("discovery_api.parser_functions.aiohttp.ClientSession") as session_ctor:
                session_mock = MagicMock()
                session_ctor.return_value = session_mock
                sender = AsyncSender("https://example.com/hook", api_key="")
                self.assertTrue(session_ctor.called)

                captured: dict = {}

                def _post(url: str, **kwargs):
                    captured["url"] = url
                    captured.update(kwargs)
                    return _Resp()

                sender.session.post.side_effect = _post

                await sender.send_message({"a": 1})
                headers = captured.get("headers")
                self.assertTrue(headers is None or "X-API-Key" not in headers)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()

