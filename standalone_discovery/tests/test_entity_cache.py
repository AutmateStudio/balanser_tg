"""Регрессия на инцидент 2026-08-11: массовый одновременный старт сессий после
восстановления clump'ов давал sqlite3.OperationalError: database is locked
из-за WAL-less соединений и DELETE-чистки на каждый вызов. См.
docs/discovery-vps104.local.md (lidogen_site)."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from discovery_api import entity_cache


class EntityCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.path)  # entity_cache сам создаст файл при первом обращении

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass

    def test_roundtrip(self) -> None:
        entity_cache.set_cached_chat_id("durov", -1001, db_path=self.path)
        got = entity_cache.get_cached_chat_ids(["@durov", "unknown"], db_path=self.path)
        self.assertEqual(got, {"durov": -1001})

    def test_wal_mode_enabled(self) -> None:
        entity_cache.init_entity_cache_db(self.path)
        with sqlite3.connect(self.path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_cleanup_is_probabilistic_not_every_call(self) -> None:
        """С вероятностью чистки 0 — DELETE не должен выполняться на обычных
        вызовах. Раньше DELETE+commit шёл на КАЖДЫЙ вызов — это и создавало
        writer-лок при конкурентном старте многих сессий одновременно."""
        entity_cache._CLEANUP_PROBABILITY = 0.0
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS entity_cache "
                    "(username TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, "
                    "resolved_at TEXT NOT NULL DEFAULT (datetime('now')))"
                )
                conn.execute(
                    "INSERT INTO entity_cache VALUES ('stale', -1, datetime('now', '-30 days'))"
                )
                conn.commit()

            # 20 обычных обращений подряд — просроченная запись должна остаться,
            # т.к. вероятность чистки выставлена в 0.
            for _ in range(20):
                entity_cache.get_cached_chat_ids(["@durov"], db_path=self.path)

            with sqlite3.connect(self.path) as conn:
                count = conn.execute(
                    "SELECT count(*) FROM entity_cache WHERE username='stale'"
                ).fetchone()[0]
            self.assertEqual(count, 1, "чистка не должна запускаться на каждый вызов")
        finally:
            entity_cache._CLEANUP_PROBABILITY = float(
                os.getenv("ENTITY_CACHE_CLEANUP_PROBABILITY", "0.005")
            )

    def test_force_cleanup_removes_expired(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS entity_cache "
                "(username TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, "
                "resolved_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            conn.execute(
                "INSERT INTO entity_cache VALUES ('stale', -1, datetime('now', '-30 days'))"
            )
            conn.commit()

        entity_cache.init_entity_cache_db(self.path, force_cleanup=True)

        with sqlite3.connect(self.path) as conn:
            count = conn.execute(
                "SELECT count(*) FROM entity_cache WHERE username='stale'"
            ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
