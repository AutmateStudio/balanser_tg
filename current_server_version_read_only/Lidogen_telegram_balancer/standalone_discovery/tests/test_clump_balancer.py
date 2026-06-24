"""Тесты балансировщика SessionClump: flood/health-aware выбор, миграция, supervisor."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import telethon.errors as te
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from discovery_api.session_health import SessionStatus


def _run(coro):
    return asyncio.run(coro)


class StatusHealthSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["PARSER_PERSISTENCE_ENABLED"] = "0"
        from discovery_api.parser_router import parser_router

        app = FastAPI()
        app.include_router(parser_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        from discovery_api.parser_router import _jobs

        _jobs.clear()
        os.environ.pop("PARSER_PERSISTENCE_ENABLED", None)

    def test_status_contains_health_summary(self) -> None:
        from discovery_api.parser_router import _ClumpJob, _jobs
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2"], "c", webhook_url="http://h")
        pc1, pc2 = clump.parser_client_list
        pc1.channels = ["@a"]
        pc1.health.mark_connected()
        pc2.health.mark_banned("ban")
        task = MagicMock()
        task.done.return_value = False
        pc1._supervisor_task = task  # type: ignore[assignment]
        _jobs["pid"] = _ClumpJob(clump=clump, parser_id="pid")

        resp = self.client.get("/discovery-api/parser/status/pid")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("health_summary", body)
        summary = body["health_summary"]
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["healthy"], 1)
        self.assertIn("/s2", summary["banned"])
        per_session = body["per_session"]
        self.assertTrue(any("health" in s for s in per_session))


class PickTargetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def test_pick_skips_unhealthy_sessions(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2", "/s3"], "c", webhook_url="http://h")
        pc1, pc2, pc3 = clump.parser_client_list
        pc1.health.mark_banned("ban")
        pc2.health.mark_flood(120)
        pc3.health.mark_connected()

        chosen = clump._pick_target()
        self.assertEqual(chosen.session_name, "/s3")

    async def test_no_healthy_raises(self) -> None:
        from discovery_api.session_registry import NoHealthySessionError, SessionClump

        clump = SessionClump(["/s1", "/s2"], "c", webhook_url="http://h")
        clump.parser_client_list[0].health.mark_banned("ban")
        clump.parser_client_list[1].health.mark_disconnected()

        with self.assertRaises(NoHealthySessionError):
            clump._pick_target()


class MigrateChannelsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def test_migrate_moves_channels_to_healthy(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2"], "c", webhook_url="http://h")
        src, dst = clump.parser_client_list
        # На src два канала.
        src.channels = ["@a", "@b"]
        src.allowed_chat_ids = {-100, -200}
        src.ref_to_chat_id = {"@a": -100, "@b": -200}
        clump.assignments = {"@a": "/s1", "@b": "/s1"}
        src.health.mark_banned("ban")
        dst.health.mark_connected()

        async def _fake_add(self, raw, *, webhook_url=None):
            cid = -999
            self.channels.append(raw)
            self.allowed_chat_ids.add(cid)
            self.ref_to_chat_id[raw] = cid
            return cid, None

        with patch(
            "discovery_api.session_registry.Parser_client.add_channel", _fake_add
        ), patch(
            "discovery_api.session_registry.Parser_client.start",
            new_callable=AsyncMock,
        ):
            result = await clump.migrate_channels("/s1", "banned")

        self.assertEqual(sorted(result["migrated"]), ["@a", "@b"])
        self.assertEqual(result["pending"], [])
        self.assertEqual(clump.assignments["@a"], "/s2")
        self.assertEqual(clump.assignments["@b"], "/s2")
        self.assertEqual(src.channels, [])

    async def test_migrate_no_healthy_goes_pending(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2"], "c", webhook_url="http://h")
        src, dst = clump.parser_client_list
        src.channels = ["@a"]
        src.allowed_chat_ids = {-100}
        src.ref_to_chat_id = {"@a": -100}
        clump.assignments = {"@a": "/s1"}
        src.health.mark_banned("ban")
        dst.health.mark_banned("ban")  # приёмник тоже недоступен

        result = await clump.migrate_channels("/s1", "banned")
        self.assertEqual(result["migrated"], [])
        self.assertEqual(clump.pending_channels, ["@a"])

    async def test_retry_pending_places_on_healthy(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        clump.pending_channels = ["@a"]
        pc = clump.parser_client_list[0]
        pc.health.mark_connected()

        async def _fake_add(self, raw, *, webhook_url=None):
            self.channels.append(raw)
            self.ref_to_chat_id[raw] = -1
            self.allowed_chat_ids.add(-1)
            return -1, None

        with patch(
            "discovery_api.session_registry.Parser_client.add_channel", _fake_add
        ), patch(
            "discovery_api.session_registry.Parser_client.start",
            new_callable=AsyncMock,
        ):
            result = await clump.retry_pending_channels()

        self.assertEqual(result["migrated"], ["@a"])
        self.assertEqual(clump.pending_channels, [])


class BatchResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def test_quota_does_not_crash_and_goes_pending(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        pc = clump.parser_client_list[0]
        pc.health.mark_connected()
        # Сессия уже под завязку.
        with patch(
            "discovery_api.session_registry.get_max_channels_per_session",
            return_value=1,
        ):
            pc.channels = ["@already"]
            # Батч не должен бросить исключение, канал уходит в pending.
            batch = await clump.add_channels_batch(["@overflow"])

        self.assertEqual(batch["added"], [])
        self.assertIn("@overflow", batch["pending"])
        self.assertIn("@overflow", clump.pending_channels)

    async def test_no_healthy_session_goes_pending(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        clump.parser_client_list[0].health.mark_banned("ban")

        batch = await clump.add_channels_batch(["@a", "@b"])
        self.assertEqual(batch["added"], [])
        self.assertEqual(sorted(batch["pending"]), ["@a", "@b"])

    async def test_flood_marks_health_and_pending(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        pc = clump.parser_client_list[0]
        pc.health.mark_connected()

        async def _flood_resolve(client, raw):
            return None, f"FloodWait 60s при resolve '{raw}'"

        with patch(
            "discovery_api.session_registry.get_or_create_client",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ), patch(
            "discovery_api.session_registry.get_session_resolve_min_interval",
            return_value=0.0,
        ), patch(
            "discovery_api.parser_functions.resolve_channel_to_chat_id",
            new=_flood_resolve,
        ):
            batch = await clump.add_channels_batch(["@a"])

        self.assertEqual(batch["added"], [])
        self.assertIn("@a", batch["pending"])
        self.assertTrue(pc.health.in_flood())


class ClumpConfigTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def test_override_changes_pick_target_limit(self) -> None:
        from discovery_api.session_registry import ChannelQuotaExceeded, SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        pc = clump.parser_client_list[0]
        pc.health.mark_connected()
        pc.channels = ["@a", "@b"]  # 2 канала

        # Дефолт (500) — ещё есть место.
        self.assertIs(clump._pick_target(), pc)

        # Переопределяем лимит до 2 — теперь сессия "полна".
        clump.update_config(max_channels_per_session=2)
        with self.assertRaises(ChannelQuotaExceeded):
            clump._pick_target()

    async def test_update_config_returns_snapshot_and_overrides(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        snap = clump.update_config(resolve_min_interval=2.5, auto_migrate=False)
        self.assertEqual(snap["resolve_min_interval"], 2.5)
        self.assertFalse(snap["auto_migrate"])
        self.assertIn("resolve_min_interval", snap["overridden"])
        self.assertIn("auto_migrate", snap["overridden"])
        # Не переопределённое поле берётся из env-дефолта.
        self.assertNotIn("max_reconnects", snap["overridden"])

    async def test_config_persists_only_overrides(self) -> None:
        from discovery_api.parser_store import clump_to_record
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        clump.update_config(max_channels_per_session=42)
        rec = clump_to_record(clump, parser_id="pid")
        self.assertEqual(rec["config"], {"max_channels_per_session": 42})

    async def test_restore_applies_config(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        clump.restore_from_record(
            {
                "webhook_url": "http://h",
                "channel_list": [],
                "assignments": {},
                "allowed_chat_ids": [],
                "config": {"max_channels_per_session": 7, "auto_migrate": False},
            }
        )
        self.assertEqual(clump.config.eff_max_channels_per_session(), 7)
        self.assertFalse(clump.config.eff_auto_migrate())


class ClumpConfigEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["PARSER_PERSISTENCE_ENABLED"] = "0"
        from discovery_api.parser_router import parser_router

        app = FastAPI()
        app.include_router(parser_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        from discovery_api.parser_router import _jobs

        _jobs.clear()
        os.environ.pop("PARSER_PERSISTENCE_ENABLED", None)

    def _make_clump_job(self):
        from discovery_api.parser_router import _ClumpJob, _jobs
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        _jobs["pid"] = _ClumpJob(clump=clump, parser_id="pid")
        return clump

    def test_get_config_returns_effective(self) -> None:
        self._make_clump_job()
        resp = self.client.get("/discovery-api/parser/pid/config")
        self.assertEqual(resp.status_code, 200)
        cfg = resp.json()["config"]
        self.assertIn("max_channels_per_session", cfg)
        self.assertIn("overridden", cfg)

    def test_patch_config_updates_and_reflects(self) -> None:
        clump = self._make_clump_job()
        resp = self.client.patch(
            "/discovery-api/parser/pid/config",
            json={"max_channels_per_session": 123, "auto_migrate": False},
        )
        self.assertEqual(resp.status_code, 200)
        cfg = resp.json()["config"]
        self.assertEqual(cfg["max_channels_per_session"], 123)
        self.assertFalse(cfg["auto_migrate"])
        self.assertEqual(clump.config.eff_max_channels_per_session(), 123)

    def test_patch_config_validation(self) -> None:
        self._make_clump_job()
        resp = self.client.patch(
            "/discovery-api/parser/pid/config",
            json={"max_channels_per_session": 0},
        )
        self.assertEqual(resp.status_code, 422)

    def test_get_config_unknown_parser_404(self) -> None:
        resp = self.client.get("/discovery-api/parser/nope/config")
        self.assertEqual(resp.status_code, 404)


class AccountMethodTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def test_default_display_name_and_meta(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/app/sessions/Client1.session"], "c", webhook_url="http://h")
        meta = clump.get_account_meta("/app/sessions/Client1.session")
        self.assertEqual(meta["display_name"], "Client1")
        self.assertEqual(meta["description"], "")

        clump.set_account_meta(
            "/app/sessions/Client1.session",
            display_name="Основной",
            description="личный аккаунт",
        )
        meta2 = clump.get_account_meta("/app/sessions/Client1.session")
        self.assertEqual(meta2["display_name"], "Основной")
        self.assertEqual(meta2["description"], "личный аккаунт")

    async def test_set_meta_unknown_raises(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        with self.assertRaises(ValueError):
            clump.set_account_meta("/nope", display_name="x")

    async def test_account_summary_and_detail(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        pc = clump.parser_client_list[0]
        pc.health.mark_connected()
        pc.channels = ["@a", "@b"]
        summaries = clump.list_account_summaries()
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["channel_count"], 2)
        self.assertEqual(summaries[0]["status"], SessionStatus.HEALTHY)

        detail = clump.account_detail("/s1")
        self.assertEqual(detail["channel_count"], 2)
        self.assertIn("max_channels_per_session", detail["limits"])
        self.assertIn("status", detail["health"])

    async def test_meta_persisted_and_restored(self) -> None:
        from discovery_api.parser_store import clump_to_record
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        clump.set_account_meta("/s1", display_name="A1", description="desc")
        rec = clump_to_record(clump, parser_id="pid")
        self.assertEqual(rec["account_meta"]["/s1"]["display_name"], "A1")

        clump2 = SessionClump(["/s1"], "c", webhook_url="http://h")
        clump2.restore_from_record(rec)
        self.assertEqual(clump2.get_account_meta("/s1")["display_name"], "A1")
        self.assertEqual(clump2.get_account_meta("/s1")["description"], "desc")


class AccountEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["PARSER_PERSISTENCE_ENABLED"] = "0"
        from discovery_api.parser_router import parser_router

        app = FastAPI()
        app.include_router(parser_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        from discovery_api.parser_router import _jobs

        _jobs.clear()
        os.environ.pop("PARSER_PERSISTENCE_ENABLED", None)

    def _make_clump_job(self, sessions=("/s1", "/s2")):
        from discovery_api.parser_router import _ClumpJob, _jobs
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(list(sessions), "c", webhook_url="http://h")
        for pc in clump.parser_client_list:
            pc.health.mark_connected()
        clump.parser_client_list[0].channels = ["@a", "@b"]
        _jobs["pid"] = _ClumpJob(clump=clump, parser_id="pid")
        return clump

    def test_accounts_list(self) -> None:
        self._make_clump_job()
        resp = self.client.get("/discovery-api/parser/accounts")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 2)
        names = {a["session_name"] for a in data["accounts"]}
        self.assertEqual(names, {"/s1", "/s2"})
        s1 = next(a for a in data["accounts"] if a["session_name"] == "/s1")
        self.assertEqual(s1["channel_count"], 2)
        self.assertEqual(s1["parser_id"], "pid")

    def test_account_detail(self) -> None:
        self._make_clump_job()
        resp = self.client.get(
            "/discovery-api/parser/account-detail",
            params={"session_name": "/s1"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["display_name"], "s1")
        self.assertIn("max_channels_per_session", data["limits"])

    def test_account_detail_not_found(self) -> None:
        self._make_clump_job()
        resp = self.client.get(
            "/discovery-api/parser/account-detail",
            params={"session_name": "/nope"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_account_channels(self) -> None:
        self._make_clump_job()
        resp = self.client.get(
            "/discovery-api/parser/account-channels",
            params={"session_name": "/s1", "parser_id": "pid"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["channel_count"], 2)
        self.assertEqual(data["channels"], ["@a", "@b"])

    def test_account_meta_patch(self) -> None:
        clump = self._make_clump_job()
        resp = self.client.patch(
            "/discovery-api/parser/account-meta",
            json={
                "parser_id": "pid",
                "session_name": "/s1",
                "display_name": "Главный",
                "description": "rkn",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["display_name"], "Главный")
        self.assertEqual(clump.get_account_meta("/s1")["description"], "rkn")

    def test_settings_global(self) -> None:
        resp = self.client.get("/discovery-api/parser/settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("max_channels_per_session", data["settings"])
        self.assertIn("flood_migrate_threshold_seconds", data["settings"])
        self.assertIn("max_reconnects", data["descriptions"])
        self.assertIn("add_channels_per_hour", data["settings"])
        self.assertIn("rebalance_enabled", data["settings"])


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def test_ban_marks_health_and_calls_on_down(self) -> None:
        from discovery_api.session_registry import Parser_client

        on_down = AsyncMock()
        pc = Parser_client("/s1", on_down=on_down)

        fake_client = object()

        with patch(
            "discovery_api.session_registry.get_or_create_client",
            new_callable=AsyncMock,
            return_value=fake_client,
        ), patch(
            "discovery_api.parser_functions.run_session_listener",
            new_callable=AsyncMock,
            side_effect=te.UserDeactivatedBanError(request=None),
        ):
            await pc._supervise("http://h")

        self.assertEqual(pc.health.status, SessionStatus.BANNED)
        self.assertTrue(pc.health.banned)
        on_down.assert_awaited_once()

    async def test_transient_then_stop(self) -> None:
        from discovery_api.session_registry import Parser_client

        pc = Parser_client("/s1")
        calls = {"n": 0}

        async def _listener(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("net down")
            # Второй заход: имитируем запрос остановки, чтобы выйти из цикла.
            pc._stop_requested = True

        with patch(
            "discovery_api.session_registry.get_or_create_client",
            new_callable=AsyncMock,
            return_value=object(),
        ), patch(
            "discovery_api.parser_functions.run_session_listener",
            side_effect=_listener,
        ), patch.object(pc, "_sleep_interruptible", new_callable=AsyncMock):
            await pc._supervise("http://h")

        self.assertGreaterEqual(calls["n"], 2)
        self.assertGreaterEqual(pc.health.reconnect_count, 1)


class RebalanceIdleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def asyncTearDown(self) -> None:
        from discovery_api import session_registry as sr

        sr.reset_for_tests()

    async def test_gap_too_small_skips(self) -> None:
        from unittest.mock import patch

        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2"], "c", webhook_url="http://h")
        for pc in clump.parser_client_list:
            pc.health.mark_connected()
        clump.update_config(
            rebalance_enabled=True,
            rebalance_min_gap_channels=50,
            max_channels_per_session=100,
            rebalance_high_watermark_ratio=0.9,
            rebalance_low_watermark_ratio=0.6,
        )
        clump.parser_client_list[0].channels = ["@a"] * 55
        clump.parser_client_list[1].channels = ["@b"] * 30
        with patch.object(clump, "_in_idle_window", return_value=True):
            result = await clump.rebalance_idle()
        self.assertEqual(result["skipped"], "gap_too_small")

    async def test_recent_move_skipped(self) -> None:
        import time
        from unittest.mock import patch

        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2"], "c", webhook_url="http://h")
        s1, s2 = clump.parser_client_list
        for pc in (s1, s2):
            pc.health.mark_connected()
        clump.update_config(
            rebalance_enabled=True,
            rebalance_min_gap_channels=10,
            max_channels_per_session=100,
            rebalance_high_watermark_ratio=0.9,
            rebalance_low_watermark_ratio=0.1,
            rebalance_cooldown_hours=24,
        )
        ref = "@last"
        s1.channels = ["@x"] * 89 + [ref]
        s1.ref_to_chat_id[ref] = 999
        s1.allowed_chat_ids.add(999)
        clump._channel_rebalance_at[ref] = time.time()
        with patch.object(clump, "_in_idle_window", return_value=True):
            result = await clump.rebalance_idle()
        self.assertEqual(result["moved"], [])

    async def test_rebalance_moves_channel(self) -> None:
        from unittest.mock import AsyncMock, patch

        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2"], "c", webhook_url="http://h")
        s1, s2 = clump.parser_client_list
        for pc in (s1, s2):
            pc.health.mark_connected()
        clump.update_config(
            rebalance_enabled=True,
            rebalance_min_gap_channels=10,
            max_channels_per_session=100,
            rebalance_high_watermark_ratio=0.9,
            rebalance_low_watermark_ratio=0.1,
        )
        ref = "@moveme"
        s1.channels = ["@x"] * 89 + [ref]
        s1.ref_to_chat_id[ref] = 777
        s1.allowed_chat_ids.add(777)

        async def _fake_add(raw: str, *, webhook_url=None):
            s2.allowed_chat_ids.add(777)
            s2.ref_to_chat_id[raw] = 777
            s2.channels.append(raw)
            return 777, None

        with patch.object(clump, "_in_idle_window", return_value=True), patch.object(
            s2, "add_channel", side_effect=_fake_add
        ), patch.object(s2, "start", new_callable=AsyncMock):
            result = await clump.rebalance_idle()

        self.assertIn(ref, result["moved"])
        self.assertNotIn(ref, s1.channels)
        self.assertIn(ref, s2.channels)


if __name__ == "__main__":
    unittest.main()
