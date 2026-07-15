"""Watchdog: STARTING-сессия без Telethon-клиента → auth-проба."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class HealthStartingProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr
        from discovery_api.session_registry import SessionClump

        self.sr = sr
        # Чистим глобальный реестр clump между тестами.
        for pid in list(sr._clumps.keys()):
            sr._clumps.pop(pid, None)
        sr._clients.clear()

        self.clump = SessionClump(["/s1"], "probe", webhook_url="http://h")
        sr._clumps["pid"] = self.clump
        self.pc = self.clump.parser_client_list[0]

    async def asyncTearDown(self) -> None:
        self.sr._clumps.clear()
        self.sr._clients.clear()

    async def test_starting_without_client_attempts_reauth(self) -> None:
        from discovery_api.session_health import SessionStatus

        self.assertEqual(self.pc.health.status, SessionStatus.STARTING)
        with (
            patch(
                "discovery_api.session_registry.get_account_auth_recheck_enabled",
                return_value=True,
            ),
            patch(
                "discovery_api.session_registry.get_account_auth_recheck_interval_seconds",
                return_value=0.0,
            ),
            patch(
                "discovery_api.session_registry._attempt_session_reauth",
                new_callable=AsyncMock,
                return_value=True,
            ) as attempt,
        ):
            await self.sr._health_check_once()
        attempt.assert_awaited_once_with(self.pc)

    async def test_starting_respects_reauth_interval(self) -> None:
        self.pc.health.record_reauth_attempt()
        with (
            patch(
                "discovery_api.session_registry.get_account_auth_recheck_enabled",
                return_value=True,
            ),
            patch(
                "discovery_api.session_registry.get_account_auth_recheck_interval_seconds",
                return_value=300.0,
            ),
            patch(
                "discovery_api.session_registry._attempt_session_reauth",
                new_callable=AsyncMock,
                return_value=True,
            ) as attempt,
        ):
            await self.sr._health_check_once()
        attempt.assert_not_awaited()

    async def test_healthy_with_client_skips_starting_probe(self) -> None:
        from discovery_api.session_health import SessionStatus
        from discovery_api.session_registry import _canonical_key

        self.pc.health.mark_connected()
        self.assertEqual(self.pc.health.status, SessionStatus.HEALTHY)
        fake_client = MagicMock()
        fake_client.is_connected.return_value = True
        self.sr._clients[_canonical_key(self.pc.session_name)] = fake_client
        with (
            patch(
                "discovery_api.session_registry.get_account_auth_recheck_enabled",
                return_value=True,
            ),
            patch(
                "discovery_api.session_registry.is_authorized_uncached",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "discovery_api.session_registry._attempt_session_reauth",
                new_callable=AsyncMock,
            ) as attempt,
        ):
            await self.sr._health_check_once()
        attempt.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
