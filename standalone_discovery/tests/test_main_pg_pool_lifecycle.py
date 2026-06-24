"""D12 — init_pool/close_pool в lifecycle discovery API (USE_PG_QUEUE)."""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class MainPgPoolLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Регресс D12: при USE_PG_QUEUE=true add-channels падал 500 — пул не инициализирован."""

    def _patch_startup_deps(self):
        return [
            patch("discovery_api.main.start_bot_polling_once", MagicMock()),
            patch("discovery_api.main.sync_accounts_from_disk", MagicMock()),
            patch("discovery_api.main.restore_active_sessions", AsyncMock()),
            patch("discovery_api.main.restore_persisted_parsers", AsyncMock()),
            patch("discovery_api.main.setup_parser_services", MagicMock()),
            patch("discovery_api.main.start_health_monitor", MagicMock()),
        ]

    async def test_startup_inits_pool_when_pg_queue_enabled(self) -> None:
        from discovery_api import main

        patchers = self._patch_startup_deps()
        for p in patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patchers])

        with patch("discovery_api.main.get_use_pg_queue", return_value=True), patch(
            "app_balance.queue.db.init_pool", new_callable=AsyncMock
        ) as mock_init:
            await main.on_startup()

        mock_init.assert_awaited_once()

    async def test_startup_skips_pool_when_pg_queue_disabled(self) -> None:
        from discovery_api import main

        patchers = self._patch_startup_deps()
        for p in patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patchers])

        with patch("discovery_api.main.get_use_pg_queue", return_value=False), patch(
            "app_balance.queue.db.init_pool", new_callable=AsyncMock
        ) as mock_init:
            await main.on_startup()

        mock_init.assert_not_awaited()

    async def test_shutdown_closes_pool_when_pg_queue_enabled(self) -> None:
        from discovery_api import main

        with patch("discovery_api.main.stop_bot_polling", MagicMock()), patch(
            "discovery_api.action_queue.stop_action_worker", new_callable=AsyncMock
        ), patch("discovery_api.main.release_all", new_callable=AsyncMock), patch(
            "discovery_api.main._stop_inprocess_worker", new_callable=AsyncMock
        ), patch(
            "discovery_api.main.get_use_pg_queue", return_value=True
        ), patch(
            "app_balance.queue.db.close_pool", new_callable=AsyncMock
        ) as mock_close:
            await main.on_shutdown()

        mock_close.assert_awaited_once()


class MainInprocessWorkerTests(unittest.IsolatedAsyncioTestCase):
    """D12 Вариант A: in-process worker запускается/останавливается по флагу."""

    def _patch_startup_deps(self):
        return [
            patch("discovery_api.main.start_bot_polling_once", MagicMock()),
            patch("discovery_api.main.sync_accounts_from_disk", MagicMock()),
            patch("discovery_api.main.restore_active_sessions", AsyncMock()),
            patch("discovery_api.main.restore_persisted_parsers", AsyncMock()),
            patch("discovery_api.main.setup_parser_services", MagicMock()),
            patch("discovery_api.main.start_health_monitor", MagicMock()),
            patch("app_balance.queue.db.init_pool", new_callable=AsyncMock),
        ]

    async def test_startup_starts_inprocess_worker_when_enabled(self) -> None:
        from discovery_api import main

        patchers = self._patch_startup_deps()
        for p in patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patchers])

        with patch("discovery_api.main.get_use_pg_queue", return_value=True), patch(
            "discovery_api.main.get_inprocess_worker", return_value=True
        ), patch(
            "discovery_api.main._start_inprocess_worker", new_callable=AsyncMock
        ) as mock_start:
            await main.on_startup()

        mock_start.assert_awaited_once()

    async def test_startup_skips_inprocess_worker_when_disabled(self) -> None:
        from discovery_api import main

        patchers = self._patch_startup_deps()
        for p in patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patchers])

        with patch("discovery_api.main.get_use_pg_queue", return_value=True), patch(
            "discovery_api.main.get_inprocess_worker", return_value=False
        ), patch(
            "discovery_api.main._start_inprocess_worker", new_callable=AsyncMock
        ) as mock_start:
            await main.on_startup()

        mock_start.assert_not_awaited()

    async def test_stop_inprocess_worker_signals_and_awaits(self) -> None:
        from discovery_api import main

        stop_event = asyncio.Event()

        async def _loop() -> None:
            await stop_event.wait()

        worker = MagicMock()
        worker.stop.side_effect = stop_event.set
        task = asyncio.create_task(_loop())
        main._inprocess_worker = worker
        main._inprocess_worker_task = task
        try:
            await main._stop_inprocess_worker()
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        worker.stop.assert_called_once()
        self.assertTrue(task.done())
        self.assertIsNone(main._inprocess_worker)
        self.assertIsNone(main._inprocess_worker_task)


if __name__ == "__main__":
    unittest.main()
