"""enroll/add/remove-session → sync_accounts_to_pg_best_effort; пустой clump."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.testclient import TestClient


class EnrollPgSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        os.environ["ACCOUNT_STORE_PATH"] = os.path.join(self._tmpdir, "acc.db")
        os.environ["SESSIONS_DIR"] = self._tmpdir
        os.environ["API_KEY"] = "test-key"
        os.environ["API_ID"] = "1"
        os.environ["API_HASH"] = "hash"
        # Создаём .session-файл для Client1
        open(os.path.join(self._tmpdir, "Client1.session"), "wb").close()

        from discovery_api.account_store import reset_account_db_for_tests
        from discovery_api.parser_router import _jobs, parser_router
        from discovery_api.api_key_auth import require_api_key

        reset_account_db_for_tests()
        _jobs.clear()
        self.app = FastAPI()
        self.app.include_router(parser_router, dependencies=[])
        self.client = TestClient(self.app)
        self._jobs = _jobs

    def tearDown(self) -> None:
        self._jobs.clear()
        for key in ("ACCOUNT_STORE_PATH", "SESSIONS_DIR", "API_KEY", "API_ID", "API_HASH"):
            os.environ.pop(key, None)

    def _running_clump(self, parser_id: str = "pid"):
        from discovery_api.parser_router import _ClumpJob
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s0"], "c", webhook_url="http://h")
        self._jobs[parser_id] = _ClumpJob(clump=clump, parser_id=parser_id)
        return clump

    def test_enroll_session_calls_pg_sync(self) -> None:
        from discovery_api.session_dialogs import AccountMembershipSnapshot

        clump = self._running_clump()
        sync_mock = AsyncMock(return_value=None)
        with (
            patch.object(clump, "start", new_callable=AsyncMock),
            patch(
                "discovery_api.session_dialogs.scan_account_channel_membership",
                new_callable=AsyncMock,
                return_value=AccountMembershipSnapshot(0, 0, 0),
            ),
            patch(
                "discovery_api.parser_router._persist_clump_state",
            ),
            patch(
                "discovery_api.parser_router._sync_accounts_pg",
                sync_mock,
            ),
        ):
            resp = self.client.post(
                "/discovery-api/parser/pid/enroll-session",
                json={"session_name": "Client1"},
            )
        self.assertEqual(resp.status_code, 200)
        sync_mock.assert_awaited_once()
        self.assertIn("enroll:Client1", sync_mock.await_args.args[0])

    def test_add_session_calls_pg_sync(self) -> None:
        clump = self._running_clump()
        sync_mock = AsyncMock(return_value=None)
        with (
            patch.object(clump, "add_session", new_callable=AsyncMock),
            patch("discovery_api.parser_router._persist_clump_state"),
            patch("discovery_api.parser_router._sync_accounts_pg", sync_mock),
        ):
            resp = self.client.post(
                "/discovery-api/parser/pid/add-session",
                json={"session_name": "Client1"},
            )
        self.assertEqual(resp.status_code, 200)
        sync_mock.assert_awaited_once()
        self.assertIn("add-session:", sync_mock.await_args.args[0])

    def test_remove_session_calls_pg_sync(self) -> None:
        clump = self._running_clump()
        sync_mock = AsyncMock(return_value=None)
        with (
            patch.object(clump, "remove_session", new_callable=AsyncMock),
            patch("discovery_api.parser_router._persist_clump_state"),
            patch("discovery_api.parser_router._sync_accounts_pg", sync_mock),
        ):
            resp = self.client.post(
                "/discovery-api/parser/pid/remove-session",
                json={"session_name": "/s0"},
            )
        self.assertEqual(resp.status_code, 200)
        sync_mock.assert_awaited_once()
        self.assertIn("remove-session:", sync_mock.await_args.args[0])


class EmptyClumpStartTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["API_KEY"] = "test-key"
        os.environ["API_ID"] = "1"
        os.environ["API_HASH"] = "hash"
        from discovery_api.parser_router import _jobs, parser_router

        _jobs.clear()
        self.app = FastAPI()
        self.app.include_router(parser_router, dependencies=[])
        self.client = TestClient(self.app)
        self._jobs = _jobs

    def tearDown(self) -> None:
        self._jobs.clear()
        for key in ("API_KEY", "API_ID", "API_HASH"):
            os.environ.pop(key, None)

    def test_parser_start_allows_empty_channel_list(self) -> None:
        from discovery_api.session_registry import SessionClump

        fake = SessionClump(["Client1"], "empty", webhook_url="http://h.example/")
        sync_mock = AsyncMock(return_value=None)
        with (
            patch(
                "discovery_api.parser_router.get_or_create_clump",
                new_callable=AsyncMock,
                return_value=fake,
            ),
            patch.object(fake, "start", new_callable=AsyncMock),
            patch("discovery_api.parser_router._persist_clump_state"),
            patch("discovery_api.parser_router._sync_accounts_pg", sync_mock),
            patch("discovery_api.parser_router._env_telegram_configured", return_value=True),
        ):
            resp = self.client.post(
                "/discovery-api/parser/start",
                json={
                    "session_name": "Client1",
                    "channel_list": [],
                    "webhook_url": "http://h.example/",
                    "clump_name": "empty-clump",
                },
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn("parser_id", body)
        self.assertIn("без каналов", body["detail"])
        sync_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
