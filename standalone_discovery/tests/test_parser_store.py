"""Тесты JSON-хранилища парсеров."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from discovery_api import parser_store


class ParserStoreTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in ("PARSER_STORE_PATH", "PARSER_PERSISTENCE_ENABLED"):
            os.environ.pop(key, None)

    def test_roundtrip_upsert_delete(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump([], f)
            os.environ["PARSER_STORE_PATH"] = path
            os.environ["PARSER_PERSISTENCE_ENABLED"] = "1"

            self.assertEqual(parser_store.load_persisted_jobs(), [])

            parser_store.upsert_job(
                parser_store.job_to_record(
                    parser_id="a1",
                    session_name="/s",
                    webhook_url="http://h",
                    channel_list=["@x"],
                    allowed_chat_ids={-1001},
                )
            )
            jobs = parser_store.load_persisted_jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["parser_id"], "a1")
            self.assertEqual(jobs[0]["allowed_chat_ids"], [-1001])
            self.assertEqual(jobs[0]["schema_version"], 2)

            parser_store.upsert_job(
                {
                    "parser_id": "a1",
                    "session_name": "/s",
                    "webhook_url": "http://h",
                    "channel_list": ["@x", "-1002"],
                    "allowed_chat_ids": [-1002, -1001],
                }
            )
            jobs2 = parser_store.load_persisted_jobs()
            self.assertEqual(len(jobs2), 1)
            self.assertEqual(len(jobs2[0]["channel_list"]), 2)

            parser_store.delete_job("a1")
            self.assertEqual(parser_store.load_persisted_jobs(), [])
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_disabled_no_write(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)
        try:
            os.environ["PARSER_STORE_PATH"] = path
            os.environ["PARSER_PERSISTENCE_ENABLED"] = "0"
            parser_store.upsert_job(
                {
                    "parser_id": "x",
                    "session_name": "s",
                    "webhook_url": "http://x",
                    "channel_list": ["1"],
                    "allowed_chat_ids": [],
                }
            )
            self.assertFalse(os.path.isfile(path))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_normalize_legacy_record(self) -> None:
        legacy = {
            "parser_id": "x",
            "session_name": "/legacy",
            "webhook_url": "http://h",
            "channel_list": ["@a"],
            "allowed_chat_ids": [-1001],
        }
        norm = parser_store.normalize_persisted_record(legacy)
        self.assertEqual(norm["session_name_list"], ["/legacy"])
        self.assertEqual(norm["schema_version"], 2)

    def test_clump_to_record(self) -> None:
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1", "/s2"], "my-clump", webhook_url="http://h")
        clump.assignments = {"@a": "/s1"}
        clump.parser_client_list[0].channels = ["@a"]
        clump.parser_client_list[0].allowed_chat_ids = {-1001}

        rec = parser_store.clump_to_record(clump, parser_id="pid-1")
        self.assertEqual(rec["parser_id"], "pid-1")
        self.assertEqual(rec["session_name_list"], ["/s1", "/s2"])
        self.assertEqual(rec["assignments"], {"@a": "/s1"})
        self.assertEqual(rec["schema_version"], 2)


class ParserRestoreTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in (
            "PARSER_STORE_PATH",
            "PARSER_PERSISTENCE_ENABLED",
            "API_ID",
            "API_HASH",
        ):
            os.environ.pop(key, None)
        from discovery_api import parser_router

        parser_router._jobs.clear()

    def test_restore_registers_job(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from discovery_api import parser_router

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    [
                        {
                            "parser_id": "pid1",
                            "session_name": "mysession",
                            "webhook_url": "http://example.com/hook",
                            "channel_list": ["-100555"],
                            "allowed_chat_ids": [-100555, -100777],
                        }
                    ],
                    f,
                )

            os.environ["PARSER_STORE_PATH"] = path
            os.environ["PARSER_PERSISTENCE_ENABLED"] = "1"
            os.environ["API_ID"] = "12345"
            os.environ["API_HASH"] = "abcdef"

            parser_router._jobs.clear()
            mock_client = AsyncMock()

            async def _run() -> None:
                with patch.object(
                    parser_router,
                    "get_or_create_clump",
                    new_callable=AsyncMock,
                ) as mock_create:
                    from discovery_api.session_registry import SessionClump

                    clump = SessionClump(
                        ["mysession"], "restore", webhook_url="http://example.com/hook"
                    )
                    clump.restore_from_record(
                        {
                            "webhook_url": "http://example.com/hook",
                            "channel_list": ["-100555"],
                            "allowed_chat_ids": [-100555, -100777],
                            "assignments": {},
                        }
                    )
                    mock_create.return_value = clump
                    with patch.object(
                        clump, "start", new_callable=AsyncMock
                    ):
                        await parser_router.restore_persisted_parsers()

            asyncio.run(_run())

            self.assertIn("pid1", parser_router._jobs)
            job = parser_router._jobs["pid1"]
            self.assertEqual(job.clump.session_name_list, ["mysession"])
            self.assertIn(-100777, job.clump.all_allowed_chat_ids())
            self.assertIn(-100555, job.clump.all_allowed_chat_ids())
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
            parser_router._jobs.clear()
