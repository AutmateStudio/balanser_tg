import asyncio
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class EntityCacheTests(unittest.TestCase):
    def test_entity_cache_roundtrip(self) -> None:
        from discovery_api import entity_cache

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            entity_cache.init_entity_cache_db(db_path)
            entity_cache.set_cached_chat_id("@testname", -100123, db_path=db_path)

            got = entity_cache.get_cached_chat_ids(["testname"], db_path=db_path)
            self.assertEqual(got.get("testname"), -100123)
        finally:
            # На Windows sqlite может удерживать файл чуть дольше — дадим небольшой ретрай.
            for _ in range(10):
                try:
                    os.unlink(db_path)
                    break
                except PermissionError:
                    time.sleep(0.05)


class ListenerFilterTests(unittest.TestCase):
    def test_handler_skips_unknown_chat(self) -> None:
        from discovery_api.parser_functions import _make_new_message_handler

        async def _run() -> None:
            q: asyncio.Queue = asyncio.Queue(maxsize=10)
            allowed = {-1001}
            handler = _make_new_message_handler(allowed_chat_ids=allowed, queue=q, webhook_url="http://hook")

            ev = SimpleNamespace(
                chat_id=-9999,
                sender_id=1,
                is_private=False,
                is_group=False,
                is_channel=True,
                message=SimpleNamespace(
                    id=1,
                    message="hi",
                    raw_text="hi",
                    date=None,
                    reply_to=None,
                ),
            )
            await handler(ev)
            self.assertTrue(q.empty())

        asyncio.run(_run())

    def test_handler_enqueues_known_chat(self) -> None:
        from discovery_api.parser_functions import _make_new_message_handler

        async def _run() -> None:
            q: asyncio.Queue = asyncio.Queue(maxsize=10)
            allowed = {-1001}
            handler = _make_new_message_handler(allowed_chat_ids=allowed, queue=q, webhook_url="http://hook")

            ev = SimpleNamespace(
                chat_id=-1001,
                sender_id=1,
                is_private=False,
                is_group=False,
                is_channel=True,
                message=SimpleNamespace(
                    id=1,
                    message="hi",
                    raw_text="hi",
                    date=None,
                    reply_to=None,
                ),
            )
            await handler(ev)
            item = q.get_nowait()
            self.assertEqual(item["webhook_url"], "http://hook")
            self.assertEqual(item["telegram_message"]["chat_id"], -1001)

        asyncio.run(_run())


class DispatchPoolTests(unittest.TestCase):
    def test_parallel_workers_are_faster_than_serial(self) -> None:
        import discovery_api.parser_functions as pf

        async def _run() -> None:
            # подготовим изолированную очередь и подменим sender
            pf._message_queue = asyncio.Queue(maxsize=1000)
            pf._worker_tasks.clear()

            async def slow_send(_: dict) -> None:
                await asyncio.sleep(0.1)

            pf.send_message_to_webhook = slow_send  # type: ignore[assignment]

            pf._ensure_dispatch_workers(3)

            t0 = time.perf_counter()
            for i in range(30):
                pf._message_queue.put_nowait({"webhook_url": "http://hook", "i": i})

            await asyncio.wait_for(pf._message_queue.join(), timeout=10)
            elapsed = time.perf_counter() - t0

            # При 1 воркере было бы около 3 секунд; при 3 — заметно меньше
            self.assertLess(elapsed, 2.0)

            for t in list(pf._worker_tasks):
                t.cancel()
            await asyncio.gather(*pf._worker_tasks, return_exceptions=True)

        asyncio.run(_run())


class ParserStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_parser_status_includes_queue_and_stats(self) -> None:
        import discovery_api.parser_functions as pf
        import discovery_api.parser_router as pr
        from discovery_api.session_registry import SessionClump

        pf._message_queue = asyncio.Queue(maxsize=10)
        pf._stats.clear()
        pf._stats.update({"enqueued": 7, "dropped": 2, "delivered": 5, "webhook_errors": 1})
        pf._message_queue.put_nowait({"webhook_url": "http://hook", "i": 1})
        pf._message_queue.put_nowait({"webhook_url": "http://hook", "i": 2})

        parser_id = "testid1"
        clump = SessionClump(["s1"], "status-clump", webhook_url="http://hook")
        pc = clump.parser_client_list[0]
        pc.channels = ["-1001"]
        pc.allowed_chat_ids = {-1001}

        async def _noop() -> None:
            await asyncio.sleep(3600)

        pc._supervisor_task = asyncio.create_task(_noop())
        pr._jobs[parser_id] = pr._ClumpJob(clump=clump, parser_id=parser_id)

        item = await pr.parser_status(parser_id)
        self.assertIn("enqueued", item.stats)
        self.assertEqual(item.stats["dropped"], 2)
        self.assertEqual(item.queue_size, 2)
        self.assertEqual(item.session_name_list, ["s1"])

        await pc.stop()
        pr._jobs.pop(parser_id, None)


if __name__ == "__main__":
    unittest.main()

