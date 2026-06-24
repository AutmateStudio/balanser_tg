"""Тесты account_store, account_registry и admin API аккаунтов."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.testclient import TestClient

class AccountStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        os.environ["ACCOUNT_STORE_PATH"] = os.path.join(self._tmpdir, "acc.db")
        from discovery_api.account_store import reset_account_db_for_tests

        reset_account_db_for_tests()

    def tearDown(self) -> None:
        os.environ.pop("ACCOUNT_STORE_PATH", None)

    def test_upsert_and_block(self) -> None:
        from discovery_api.account_store import get_account, set_admin_blocked, upsert_account

        upsert_account("Client1", display_name="C1", max_channels=10)
        rec = get_account("Client1")
        self.assertEqual(rec["display_name"], "C1")
        self.assertEqual(rec["max_channels"], 10)
        set_admin_blocked("Client1", blocked=True, reason="test")
        rec2 = get_account("Client1")
        self.assertTrue(rec2["admin_blocked"])
        self.assertEqual(rec2["block_reason"], "test")


class AccountRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        os.environ["ACCOUNT_STORE_PATH"] = os.path.join(self._tmpdir, "acc.db")
        os.environ["SESSIONS_DIR"] = self._tmpdir
        from discovery_api.account_store import reset_account_db_for_tests

        reset_account_db_for_tests()
        open(os.path.join(self._tmpdir, "Alpha.session"), "wb").close()

    def tearDown(self) -> None:
        os.environ.pop("ACCOUNT_STORE_PATH", None)
        os.environ.pop("SESSIONS_DIR", None)

    def test_scan_and_merge(self) -> None:
        from discovery_api.account_registry import list_all_accounts_merged, sync_accounts_from_disk

        sync_accounts_from_disk()
        rows = list_all_accounts_merged({})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_name"], "Alpha")
        self.assertFalse(rows[0]["in_clump"])

    def test_eff_channel_limit_from_account_store(self) -> None:
        from discovery_api.account_registry import eff_channel_limit_info
        from discovery_api.account_store import upsert_account

        upsert_account("Alpha", max_channels=15)
        limit, source = eff_channel_limit_info("Alpha", clump_limit=500)
        self.assertEqual(limit, 15)
        self.assertEqual(source, "account")

    def test_register_after_qr_and_delete_full(self) -> None:
        from discovery_api.account_registry import (
            delete_account_full,
            register_account_after_qr,
            session_file_exists,
        )
        from discovery_api.account_store import get_account

        open(os.path.join(self._tmpdir, "QrUser.session"), "wb").close()
        rec = register_account_after_qr("QrUser")
        self.assertEqual(rec["source"], "qr")
        self.assertTrue(session_file_exists("QrUser"))
        delete_account_full("QrUser")
        self.assertFalse(session_file_exists("QrUser"))
        self.assertIsNone(get_account("QrUser"))


class AdminBlockPickTargetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def test_admin_block_excludes_from_pick(self) -> None:
        from discovery_api.account_store import upsert_account
        from discovery_api.session_registry import NoHealthySessionError, SessionClump

        upsert_account("s1", admin_blocked=True)
        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        clump.parser_client_list[0].health.mark_connected()
        with self.assertRaises(NoHealthySessionError):
            clump._pick_target()


class PerAccountLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def test_per_account_max_channels(self) -> None:
        from discovery_api.account_store import upsert_account
        from discovery_api.session_registry import ChannelQuotaExceeded, SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        clump.parser_client_list[0].health.mark_connected()
        upsert_account("s1", max_channels=2)
        clump.parser_client_list[0].channels = ["@a", "@b"]
        with self.assertRaises(ChannelQuotaExceeded):
            clump._pick_target()


class HourlyQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def test_hourly_quota_blocks_pick(self) -> None:
        from discovery_api.session_registry import NoHealthySessionError, SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        pc = clump.parser_client_list[0]
        pc.health.mark_connected()
        clump.update_config(add_channels_per_hour=1)
        pc.record_channel_add()
        with self.assertRaises(NoHealthySessionError):
            clump._pick_target()

    async def test_hourly_quota_batch_goes_pending(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        pc = clump.parser_client_list[0]
        pc.health.mark_connected()
        clump.update_config(add_channels_per_hour=1)
        pc.record_channel_add()

        batch = await clump.add_channels_batch(["@quota_overflow"])

        self.assertEqual(batch["added"], [])
        self.assertIn("@quota_overflow", batch["pending"])
        self.assertIn("@quota_overflow", clump.pending_channels)


class RemoveSessionForceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def test_remove_session_force_migrates_and_removes(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2"], "c", webhook_url="http://h")
        src, dst = clump.parser_client_list
        src.channels = ["@a"]
        src.allowed_chat_ids = {-100}
        src.ref_to_chat_id = {"@a": -100}
        clump.assignments = {"@a": "/s1"}
        src.health.mark_connected()
        dst.health.mark_connected()
        clump.update_config(auto_migrate=True)

        async def _fake_add(self, raw, *, webhook_url=None):
            self.channels.append(raw)
            self.ref_to_chat_id[raw] = -100
            self.allowed_chat_ids.add(-100)
            return -100, None

        with patch(
            "discovery_api.session_registry.Parser_client.add_channel", _fake_add
        ), patch(
            "discovery_api.session_registry.Parser_client.start",
            new_callable=AsyncMock,
        ), patch(
            "discovery_api.session_registry.release_client",
            new_callable=AsyncMock,
        ):
            result = await clump.remove_session_force("/s1", migrate=True)

        self.assertTrue(result["removed"])
        self.assertFalse(clump.has_session("/s1"))
        self.assertEqual(clump.assignments["@a"], "/s2")

    async def test_remove_session_force_without_migrate_raises(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        clump.parser_client_list[0].channels = ["@a"]
        with self.assertRaises(ValueError):
            await clump.remove_session_force("/s1", migrate=False)


class AccountEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        os.environ["PARSER_PERSISTENCE_ENABLED"] = "0"
        os.environ["ACCOUNT_STORE_PATH"] = os.path.join(self._tmpdir, "acc.db")
        os.environ["SESSIONS_DIR"] = self._tmpdir
        from discovery_api.account_store import reset_account_db_for_tests

        reset_account_db_for_tests()
        open(os.path.join(self._tmpdir, "Client1.session"), "wb").close()
        from discovery_api.parser_router import parser_router

        app = FastAPI()
        app.include_router(parser_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        from discovery_api.parser_router import _jobs

        _jobs.clear()
        os.environ.pop("PARSER_PERSISTENCE_ENABLED", None)
        os.environ.pop("ACCOUNT_STORE_PATH", None)
        os.environ.pop("SESSIONS_DIR", None)

    def test_accounts_all(self) -> None:
        resp = self.client.get("/discovery-api/parser/accounts/all")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["total"], 1)
        names = {a["session_name"] for a in data["accounts"]}
        self.assertIn("Client1", names)

    def test_block_and_unblock(self) -> None:
        resp = self.client.patch(
            "/discovery-api/parser/accounts/Client1/block",
            json={"blocked": True, "reason": "test"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["admin_blocked"])
        resp2 = self.client.patch(
            "/discovery-api/parser/accounts/Client1/block",
            json={"blocked": False},
        )
        self.assertFalse(resp2.json()["admin_blocked"])

    def test_account_update_max_channels(self) -> None:
        resp = self.client.patch(
            "/discovery-api/parser/accounts/Client1",
            json={"display_name": "Main", "max_channels": 42},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["display_name"], "Main")
        self.assertEqual(data["max_channels"], 42)
        self.assertEqual(data["effective_max_channels"], 42)
        self.assertEqual(data["limit_source"], "account")

    def test_accounts_all_shows_clump_runtime(self) -> None:
        from discovery_api.parser_router import _ClumpJob, _jobs
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["Client1"], "c", webhook_url="http://h")
        clump.parser_client_list[0].health.mark_connected()
        clump.parser_client_list[0].channels = ["@x"]
        _jobs["pid"] = _ClumpJob(clump=clump, parser_id="pid")

        resp = self.client.get("/discovery-api/parser/accounts/all")
        self.assertEqual(resp.status_code, 200)
        row = next(a for a in resp.json()["accounts"] if a["session_name"] == "Client1")
        self.assertTrue(row["in_clump"])
        self.assertEqual(row["parser_id"], "pid")
        self.assertEqual(row["channel_count"], 1)

    def test_delete_account_removes_file_and_store(self) -> None:
        from discovery_api.account_registry import session_file_exists
        from discovery_api.account_store import get_account, upsert_account

        upsert_account("Client1", display_name="C1")
        self.assertTrue(session_file_exists("Client1"))

        resp = self.client.delete("/discovery-api/parser/accounts/Client1")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["deleted"])
        self.assertFalse(session_file_exists("Client1"))
        self.assertIsNone(get_account("Client1"))

    def test_enroll_session(self) -> None:
        from discovery_api.parser_router import _ClumpJob, _jobs
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s0"], "c", webhook_url="http://h")
        _jobs["pid"] = _ClumpJob(clump=clump, parser_id="pid")
        with patch.object(clump, "start", new_callable=AsyncMock):
            resp = self.client.post(
                "/discovery-api/parser/pid/enroll-session",
                json={"session_name": "Client1"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["in_clump"])


if __name__ == "__main__":
    unittest.main()
