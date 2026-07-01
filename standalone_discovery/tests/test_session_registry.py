"""Юнит-тесты для `discovery_api.session_registry`."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class FakeTelegramClient:
    """Подмена Telethon-клиента: счётчики __init__ и connect."""

    init_count = 0
    connect_count = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        FakeTelegramClient.init_count += 1

    async def connect(self) -> None:
        FakeTelegramClient.connect_count += 1

    def is_connected(self) -> bool:
        return True

    async def is_user_authorized(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass


class SessionRegistryConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()
        FakeTelegramClient.init_count = 0
        FakeTelegramClient.connect_count = 0

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        await sr.release_all()
        sr.reset_for_tests()

    async def test_single_connect_under_contention(self) -> None:
        from discovery_api import session_registry as sr

        with patch("discovery_api.session_registry.TelegramClient", FakeTelegramClient), patch(
            "discovery_api.session_registry.get_api_id", return_value=1
        ), patch("discovery_api.session_registry.get_api_hash", return_value="hash"):
            await asyncio.gather(
                *[sr.get_or_create_client("/app/sessions/s1") for _ in range(5)]
            )

        self.assertEqual(FakeTelegramClient.init_count, 1)
        self.assertEqual(FakeTelegramClient.connect_count, 1)

    async def test_idempotent_same_session(self) -> None:
        from discovery_api import session_registry as sr

        with patch("discovery_api.session_registry.TelegramClient", FakeTelegramClient), patch(
            "discovery_api.session_registry.get_api_id", return_value=1
        ), patch("discovery_api.session_registry.get_api_hash", return_value="hash"):
            c1 = await sr.get_or_create_client("/sess/a")
            c2 = await sr.get_or_create_client("/sess/a")
            self.assertIs(c1, c2)
        self.assertEqual(FakeTelegramClient.init_count, 1)


class SessionRegistryStringCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()
        FakeTelegramClient.init_count = 0

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        await sr.release_all()
        sr.reset_for_tests()

    async def test_get_session_string_caches_save(self) -> None:
        from discovery_api import session_registry as sr

        save_calls: list[int] = []

        def fake_save(sess: object) -> str:
            save_calls.append(1)
            return "1AbcStringSessionFake=="

        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        mock_client.is_user_authorized = AsyncMock(return_value=True)
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch("discovery_api.session_registry.TelegramClient", return_value=mock_client), patch(
            "discovery_api.session_registry.get_api_id", return_value=1
        ), patch("discovery_api.session_registry.get_api_hash", return_value="hash"), patch.object(
            sr.StringSession, "save", staticmethod(fake_save)
        ):
            s1 = await sr.get_session_string("/sess/cache")
            s2 = await sr.get_session_string("/sess/cache")

        self.assertEqual(s1, s2)
        self.assertEqual(len(save_calls), 1)


class SessionRegistryReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        await sr.release_all()
        sr.reset_for_tests()

    async def test_release_all_disconnects(self) -> None:
        from discovery_api import session_registry as sr

        disconnect_mock = AsyncMock()

        class C(FakeTelegramClient):
            async def disconnect(self) -> None:
                await disconnect_mock()

        with patch("discovery_api.session_registry.TelegramClient", C), patch(
            "discovery_api.session_registry.get_api_id", return_value=1
        ), patch("discovery_api.session_registry.get_api_hash", return_value="hash"):
            await sr.get_or_create_client("/sess/rel")
            await sr.release_all()

        disconnect_mock.assert_awaited()


class SessionRegistryUnauthorizedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        await sr.release_all()
        sr.reset_for_tests()

    async def test_unauthorized_marks_clump_health_and_raises(self) -> None:
        from discovery_api.session_health import SessionStatus
        from discovery_api import session_registry as sr

        class UnauthorizedClient(FakeTelegramClient):
            async def is_user_authorized(self) -> bool:
                return False

        notify_mock = AsyncMock()
        clump = sr.SessionClump(["/sess/u1"], "c", webhook_url="http://h")
        sr._clumps["pid"] = clump
        pc = clump.parser_client_list[0]

        with (
            patch("discovery_api.session_registry.TelegramClient", UnauthorizedClient),
            patch("discovery_api.session_registry.get_api_id", return_value=1),
            patch("discovery_api.session_registry.get_api_hash", return_value="hash"),
            patch(
                "discovery_api.session_registry._persist_unauthorized_pg",
                notify_mock,
            ),
        ):
            with self.assertRaises(RuntimeError):
                await sr.get_or_create_client("/sess/u1")

        self.assertEqual(pc.health.status, SessionStatus.ERROR)
        self.assertIn("не авторизована", pc.health.last_error or "")
        notify_mock.assert_awaited_once()

    async def test_authorized_client_triggers_reauthorize(self) -> None:
        from discovery_api import session_registry as sr

        reauth_mock = AsyncMock(return_value=True)

        with (
            patch("discovery_api.session_registry.TelegramClient", FakeTelegramClient),
            patch("discovery_api.session_registry.get_api_id", return_value=1),
            patch("discovery_api.session_registry.get_api_hash", return_value="hash"),
            patch(
                "discovery_api.session_registry.notify_session_reauthorized",
                reauth_mock,
            ),
        ):
            client = await sr.get_or_create_client("/sess/ok")
            self.assertIsNotNone(client)

        reauth_mock.assert_awaited_once_with("/sess/ok")


if __name__ == "__main__":
    unittest.main()
