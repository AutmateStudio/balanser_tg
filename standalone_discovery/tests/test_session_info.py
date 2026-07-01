"""Юнит-тесты discovery_api.session_info."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _run(coro):
    return asyncio.run(coro)


class SessionInfoProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        os.environ["SESSIONS_DIR"] = self._tmpdir
        open(os.path.join(self._tmpdir, "Alpha.session"), "wb").close()

    def tearDown(self) -> None:
        os.environ.pop("SESSIONS_DIR", None)

    def test_probe_authorized_returns_phone(self) -> None:
        from discovery_api.session_info import probe_session_info

        fake_me = MagicMock()
        fake_me.phone = "79991234567"

        fake_client = MagicMock()
        fake_client.is_user_authorized = AsyncMock(return_value=True)
        fake_client.get_me = AsyncMock(return_value=fake_me)
        fake_client.connect = AsyncMock()
        fake_client.disconnect = AsyncMock()

        with patch(
            "discovery_api.session_info.find_registered_client", return_value=None
        ), patch(
            "discovery_api.session_info.TelegramClient", return_value=fake_client
        ), patch("discovery_api.session_info.get_api_id", return_value=1), patch(
            "discovery_api.session_info.get_api_hash", return_value="hash"
        ):
            row = _run(probe_session_info("Alpha"))

        self.assertEqual(row["session_name"], "Alpha")
        self.assertEqual(row["session_file"], "Alpha.session")
        self.assertEqual(row["phone"], "+79991234567")
        fake_client.disconnect.assert_awaited_once()

    def test_probe_reuses_registered_client_without_disconnect(self) -> None:
        from discovery_api.session_info import probe_session_info

        fake_me = MagicMock()
        fake_me.phone = "70001112233"

        fake_client = MagicMock()
        fake_client.is_user_authorized = AsyncMock(return_value=True)
        fake_client.get_me = AsyncMock(return_value=fake_me)
        fake_client.disconnect = AsyncMock()

        with patch(
            "discovery_api.session_info.find_registered_client",
            return_value=fake_client,
        ):
            row = _run(probe_session_info("Alpha"))

        self.assertEqual(row["phone"], "+70001112233")
        fake_client.disconnect.assert_not_called()

    def test_probe_missing_file_raises(self) -> None:
        from discovery_api.session_info import probe_session_info

        with self.assertRaises(FileNotFoundError):
            _run(probe_session_info("Missing"))


if __name__ == "__main__":
    unittest.main()
