"""Юнит-тесты модели здоровья сессии и классификации ошибок Telethon."""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import telethon.errors as te

from discovery_api.session_health import (
    SessionHealth,
    SessionStatus,
    classify_telethon_error,
    parse_flood_wait_seconds,
)


class ParseFloodWaitSecondsTests(unittest.TestCase):
    def test_extracts_seconds_from_resolve_error(self) -> None:
        self.assertEqual(
            parse_flood_wait_seconds("FloodWait 42s при resolve '@x'"), 42
        )

    def test_extracts_with_space_variants(self) -> None:
        self.assertEqual(parse_flood_wait_seconds("FloodWait 7 s"), 7)
        self.assertEqual(parse_flood_wait_seconds("floodwait 100s"), 100)

    def test_none_for_non_flood(self) -> None:
        self.assertIsNone(parse_flood_wait_seconds("нет доступа к чату"))
        self.assertIsNone(parse_flood_wait_seconds(""))
        self.assertIsNone(parse_flood_wait_seconds(None))


class ClassifyTelethonErrorTests(unittest.TestCase):
    def test_flood_returns_seconds(self) -> None:
        exc = te.FloodWaitError(request=None, capture=42)
        kind, seconds = classify_telethon_error(exc)
        self.assertEqual(kind, "flood")
        self.assertEqual(seconds, 42)

    def test_banned_errors(self) -> None:
        for exc in (
            te.UserDeactivatedBanError(request=None),
            te.AuthKeyUnregisteredError(request=None),
            te.SessionRevokedError(request=None),
            te.PhoneNumberBannedError(request=None),
        ):
            kind, seconds = classify_telethon_error(exc)
            self.assertEqual(kind, "banned", exc)
            self.assertIsNone(seconds)

    def test_transient_errors(self) -> None:
        for exc in (ConnectionError("boom"), TimeoutError("t")):
            kind, _ = classify_telethon_error(exc)
            self.assertEqual(kind, "transient", exc)

    def test_fatal_default(self) -> None:
        kind, seconds = classify_telethon_error(ValueError("unexpected"))
        self.assertEqual(kind, "fatal")
        self.assertIsNone(seconds)


class SessionHealthTests(unittest.TestCase):
    def test_flood_lifecycle(self) -> None:
        h = SessionHealth()
        h.mark_connected()
        self.assertEqual(h.status, SessionStatus.HEALTHY)
        h.mark_flood(60)
        self.assertEqual(h.status, SessionStatus.FLOOD_WAIT)
        self.assertTrue(h.in_flood())
        self.assertFalse(h.is_available())
        self.assertEqual(h.flood_wait_count, 1)
        self.assertEqual(h.flood_wait_total_seconds, 60)
        # Принудительно истёкший флуд.
        h.flood_until = time.time() - 1
        self.assertTrue(h.clear_flood_if_expired())
        self.assertFalse(h.in_flood())
        self.assertTrue(h.is_available())

    def test_banned_blocks_availability(self) -> None:
        h = SessionHealth()
        h.mark_connected()
        h.mark_banned("UserDeactivatedBanError")
        self.assertEqual(h.status, SessionStatus.BANNED)
        self.assertTrue(h.banned)
        self.assertFalse(h.is_available())

    def test_disconnected_not_available(self) -> None:
        h = SessionHealth()
        h.mark_disconnected()
        self.assertEqual(h.status, SessionStatus.DISCONNECTED)
        self.assertFalse(h.is_available())

    def test_to_dict_has_keys(self) -> None:
        h = SessionHealth()
        d = h.to_dict()
        for key in ("status", "connected", "banned", "flood_remaining_seconds"):
            self.assertIn(key, d)


class AccountAuthWatchdogTests(unittest.TestCase):
    """Account-auth watchdog: политика повторных попыток реавторизации."""

    def test_should_attempt_reauth_true_on_first_error(self) -> None:
        h = SessionHealth()
        h.mark_unauthorized("не авторизована")
        self.assertTrue(h.should_attempt_reauth(300.0))

    def test_should_attempt_reauth_false_before_interval_elapsed(self) -> None:
        h = SessionHealth()
        h.mark_unauthorized("не авторизована")
        h.record_reauth_attempt()
        self.assertFalse(h.should_attempt_reauth(300.0))

    def test_should_attempt_reauth_true_after_interval_elapsed(self) -> None:
        h = SessionHealth()
        h.mark_unauthorized("не авторизована")
        h.record_reauth_attempt()
        h.last_reauth_attempt_at = time.time() - 301.0
        self.assertTrue(h.should_attempt_reauth(300.0))

    def test_should_attempt_reauth_false_when_banned(self) -> None:
        h = SessionHealth()
        h.mark_banned("UserDeactivatedBanError")
        self.assertFalse(h.should_attempt_reauth(0.0))

    def test_should_attempt_reauth_false_when_not_error(self) -> None:
        h = SessionHealth()
        h.mark_connected()
        self.assertFalse(h.should_attempt_reauth(0.0))

    def test_record_reauth_attempt_increments_counter(self) -> None:
        h = SessionHealth()
        h.mark_unauthorized("не авторизована")
        h.record_reauth_attempt()
        h.record_reauth_attempt()
        self.assertEqual(h.reauth_attempt_count, 2)
        self.assertIsNotNone(h.last_reauth_attempt_at)

    def test_successful_reauth_resets_error_state(self) -> None:
        h = SessionHealth()
        h.mark_unauthorized("не авторизована")
        h.record_reauth_attempt()
        self.assertTrue(h.mark_reauthorized())
        self.assertEqual(h.status, SessionStatus.HEALTHY)
        self.assertFalse(h.should_attempt_reauth(0.0))


if __name__ == "__main__":
    unittest.main()
