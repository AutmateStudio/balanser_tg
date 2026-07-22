"""Юнит-тесты для discovery_api.session_dialogs (проверка членства аккаунта)."""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _dialog(entity_id: int, *, broadcast: bool = False, megagroup: bool = False, gigagroup: bool = False):
    entity = SimpleNamespace(id=entity_id, broadcast=broadcast, megagroup=megagroup, gigagroup=gigagroup)
    return SimpleNamespace(entity=entity)


class FakeDialogsClient:
    """Подмена TelegramClient: `iter_dialogs` как асинхронный генератор."""

    def __init__(self, dialogs: list) -> None:
        self._dialogs = dialogs

    async def iter_dialogs(self):
        for d in self._dialogs:
            yield d


class FailingDialogsClient:
    async def iter_dialogs(self):
        raise ConnectionError("boom")
        yield  # noqa: unreachable — оставляет метод асинхронным генератором


class ChannelPeerIdTests(unittest.TestCase):
    def test_matches_telegram_marked_id_convention(self) -> None:
        from discovery_api.session_dialogs import _channel_peer_id

        # Реальный пример: raw channel_id 1234567890 -> "-100" + digits.
        self.assertEqual(_channel_peer_id(1234567890), -1001234567890)


class ScanClientChannelMembershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_counts_only_channels_and_supergroups(self) -> None:
        from discovery_api.session_dialogs import scan_client_channel_membership

        client = FakeDialogsClient(
            [
                _dialog(100, broadcast=True),
                _dialog(101, megagroup=True),
                _dialog(102, gigagroup=True),
                _dialog(103),  # обычный чат/ЛС — не считается каналом
            ]
        )
        snapshot = await scan_client_channel_membership(client)

        self.assertEqual(snapshot.telegram_channel_count, 3)
        self.assertEqual(snapshot.required_channel_total, 0)
        self.assertEqual(snapshot.required_channel_present, 0)
        self.assertIsNone(snapshot.error)

    async def test_cross_references_required_channels_from_clump(self) -> None:
        from discovery_api import session_registry as sr
        from discovery_api.session_dialogs import _channel_peer_id, scan_client_channel_membership

        sr.reset_for_tests()
        try:
            clump = sr.SessionClump(["/sess/a", "/sess/b"], "c", webhook_url="http://h")
            # ref_to_chat_id хранит marked peer_id (как в проде, через
            # telethon.utils.get_peer_id при resolve) — не «голые» entity.id.
            clump.parser_client_list[0].ref_to_chat_id = {
                "@ref1": _channel_peer_id(100),
                "@ref2": _channel_peer_id(101),
            }
            clump.parser_client_list[1].ref_to_chat_id = {"@ref3": _channel_peer_id(999)}

            client = FakeDialogsClient(
                [_dialog(100, broadcast=True), _dialog(200, broadcast=True)]
            )
            snapshot = await scan_client_channel_membership(client, clump)

            self.assertEqual(snapshot.telegram_channel_count, 2)
            self.assertEqual(snapshot.required_channel_total, 3)
            self.assertEqual(snapshot.required_channel_present, 1)
        finally:
            sr.reset_for_tests()

    async def test_marks_raw_entity_id_before_comparing_with_ref_to_chat_id(self) -> None:
        """Регресс: entity.id «голый» (без -100), ref_to_chat_id — marked peer_id.

        Без преобразования пересечение было бы всегда пустым, даже если канал
        на аккаунте реально есть (required_channel_present=0 при непустом clump).
        """
        from discovery_api import session_registry as sr
        from discovery_api.session_dialogs import _channel_peer_id, scan_client_channel_membership

        sr.reset_for_tests()
        try:
            clump = sr.SessionClump(["/sess/a"], "c", webhook_url="http://h")
            clump.parser_client_list[0].ref_to_chat_id = {"@ref1": _channel_peer_id(555)}

            client = FakeDialogsClient([_dialog(555, broadcast=True)])
            snapshot = await scan_client_channel_membership(client, clump)

            self.assertEqual(snapshot.required_channel_total, 1)
            self.assertEqual(snapshot.required_channel_present, 1)
        finally:
            sr.reset_for_tests()

    async def test_iter_dialogs_failure_is_captured_as_error(self) -> None:
        from discovery_api.session_dialogs import scan_client_channel_membership

        snapshot = await scan_client_channel_membership(FailingDialogsClient())

        self.assertEqual(snapshot.telegram_channel_count, 0)
        self.assertIsNotNone(snapshot.error)


class ScanAccountChannelMembershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_lookup_failure_is_captured_as_error(self) -> None:
        from discovery_api.session_dialogs import scan_account_channel_membership

        with patch(
            "discovery_api.session_dialogs.get_or_create_client",
            AsyncMock(side_effect=RuntimeError("не авторизована")),
        ):
            snapshot = await scan_account_channel_membership("Test3")

        self.assertEqual(snapshot.telegram_channel_count, 0)
        self.assertIn("не авторизована", snapshot.error or "")

    async def test_delegates_to_scan_client_channel_membership(self) -> None:
        from discovery_api.session_dialogs import scan_account_channel_membership

        fake_client = FakeDialogsClient([_dialog(1, broadcast=True)])
        with patch(
            "discovery_api.session_dialogs.get_or_create_client",
            AsyncMock(return_value=fake_client),
        ):
            snapshot = await scan_account_channel_membership("Test3")

        self.assertEqual(snapshot.telegram_channel_count, 1)
        self.assertIsNone(snapshot.error)


class RefreshAndPersistChannelCountTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmpdir = tempfile.mkdtemp()
        os.environ["ACCOUNT_STORE_PATH"] = os.path.join(self._tmpdir, "acc.db")
        from discovery_api.account_store import reset_account_db_for_tests

        reset_account_db_for_tests()

    def tearDown(self) -> None:
        os.environ.pop("ACCOUNT_STORE_PATH", None)

    async def test_persists_count_on_success(self) -> None:
        from discovery_api.account_store import get_account
        from discovery_api.session_dialogs import refresh_and_persist_channel_count

        fake_client = FakeDialogsClient(
            [_dialog(1, broadcast=True), _dialog(2, megagroup=True)]
        )
        with patch(
            "discovery_api.session_dialogs.get_or_create_client",
            AsyncMock(return_value=fake_client),
        ):
            snapshot = await refresh_and_persist_channel_count("Client1")

        self.assertEqual(snapshot.telegram_channel_count, 2)
        rec = get_account("Client1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["telegram_channel_count"], 2)
        self.assertIsNotNone(rec["telegram_channel_count_synced_at"])

    async def test_does_not_persist_on_error(self) -> None:
        from discovery_api.account_store import get_account, upsert_account
        from discovery_api.session_dialogs import refresh_and_persist_channel_count

        upsert_account("Client1")
        with patch(
            "discovery_api.session_dialogs.get_or_create_client",
            AsyncMock(side_effect=RuntimeError("не авторизована")),
        ):
            snapshot = await refresh_and_persist_channel_count("Client1")

        self.assertIsNotNone(snapshot.error)
        rec = get_account("Client1")
        self.assertIsNone(rec["telegram_channel_count"])


class FindClumpForSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def test_finds_clump_by_canonical_name_variant(self) -> None:
        from discovery_api import session_registry as sr

        clump = sr.SessionClump(["/app/sessions/Test3"], "c", webhook_url="http://h")
        sr._clumps["pid"] = clump

        found = sr.find_clump_for_session("Test3")

        self.assertIs(found, clump)

    async def test_returns_none_when_not_found(self) -> None:
        from discovery_api import session_registry as sr

        self.assertIsNone(sr.find_clump_for_session("Unknown"))


if __name__ == "__main__":
    unittest.main()
