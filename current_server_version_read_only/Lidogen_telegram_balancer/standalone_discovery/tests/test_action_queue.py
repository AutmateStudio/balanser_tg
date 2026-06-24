"""Тесты FIFO action queue."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class ActionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        os.environ["ACTION_QUEUE_DB"] = os.path.join(self._tmpdir, "q.db")
        from discovery_api.action_queue import reset_action_queue_for_tests

        reset_action_queue_for_tests()
        self._order: list[str] = []

    async def asyncTearDown(self) -> None:
        from discovery_api.action_queue import stop_action_worker

        await stop_action_worker()
        os.environ.pop("ACTION_QUEUE_DB", None)

    async def test_fifo_execution(self) -> None:
        from discovery_api.action_queue import (
            enqueue_action,
            get_action,
            register_action_handler,
            start_action_worker,
            stop_action_worker,
        )

        async def handler(item: dict) -> None:
            self._order.append(item["id"])
            await asyncio.sleep(0.05)

        register_action_handler(handler)
        start_action_worker()
        a1 = enqueue_action(
            action_type="add_channels", parser_id="p1", payload={"channel_list": ["@a"]}
        )
        a2 = enqueue_action(
            action_type="add_channels", parser_id="p1", payload={"channel_list": ["@b"]}
        )
        await asyncio.sleep(0.3)
        await stop_action_worker()
        self.assertEqual(len(self._order), 2)
        self.assertEqual(self._order[0], a1["id"])
        self.assertEqual(self._order[1], a2["id"])
        self.assertEqual(get_action(a1["id"])["status"], "done")
        self.assertEqual(get_action(a2["id"])["status"], "done")

    async def test_list_actions_filter(self) -> None:
        from discovery_api.action_queue import enqueue_action, list_actions

        a1 = enqueue_action(
            action_type="add_channels", parser_id="p1", payload={"channel_list": ["@a"]}
        )
        enqueue_action(
            action_type="remove_channels", parser_id="p2", payload={"channel_list": ["@b"]}
        )
        p1_items = list_actions(parser_id="p1")
        self.assertEqual(len(p1_items), 1)
        self.assertEqual(p1_items[0]["id"], a1["id"])
        add_items = list_actions(action_type="add_channels")
        self.assertTrue(all(i["action_type"] == "add_channels" for i in add_items))

    async def test_failed_action_records_error(self) -> None:
        from discovery_api.action_queue import (
            enqueue_action,
            get_action,
            register_action_handler,
            start_action_worker,
            stop_action_worker,
        )

        async def _fail(_item: dict) -> None:
            raise RuntimeError("boom")

        register_action_handler(_fail)
        start_action_worker()
        action = enqueue_action(
            action_type="add_channels", parser_id="p1", payload={"channel_list": ["@a"]}
        )
        await asyncio.sleep(0.3)
        await stop_action_worker()
        rec = get_action(action["id"])
        assert rec is not None
        self.assertEqual(rec["status"], "failed")
        self.assertIn("boom", rec["error"] or "")

    async def test_update_action_progress(self) -> None:
        from discovery_api.action_queue import (
            enqueue_action,
            get_action,
            register_action_handler,
            start_action_worker,
            stop_action_worker,
            update_action_progress,
        )

        async def _handler(item: dict) -> None:
            update_action_progress(item["id"], 1, 3)
            await asyncio.sleep(0.02)
            update_action_progress(item["id"], 3, 3)

        register_action_handler(_handler)
        start_action_worker()
        action = enqueue_action(
            action_type="add_channels", parser_id="p1", payload={"channel_list": ["@a"]}
        )
        await asyncio.sleep(0.3)
        await stop_action_worker()
        rec = get_action(action["id"])
        assert rec is not None
        self.assertEqual(rec["progress"]["done"], 3)
        self.assertEqual(rec["progress"]["total"], 3)


if __name__ == "__main__":
    unittest.main()
