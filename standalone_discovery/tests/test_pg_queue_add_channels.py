"""D8 — тесты USE_PG_QUEUE для async add-channels (+ batch enqueue)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.testclient import TestClient


class PgQueueAddChannelsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = os.path.join(os.path.dirname(__file__), ".tmp_pg_queue_tests")
        os.makedirs(self._tmpdir, exist_ok=True)
        os.environ["PARSER_PERSISTENCE_ENABLED"] = "0"
        os.environ["USE_PG_QUEUE"] = "true"
        os.environ["ACTION_QUEUE_DB"] = os.path.join(self._tmpdir, "q.db")

    def tearDown(self) -> None:
        from discovery_api.parser_router import _jobs

        _jobs.clear()
        for key in ("PARSER_PERSISTENCE_ENABLED", "USE_PG_QUEUE", "ACTION_QUEUE_DB"):
            os.environ.pop(key, None)

    def _make_client(self) -> TestClient:
        from discovery_api.parser_router import parser_router

        app = FastAPI()
        app.include_router(parser_router)
        return TestClient(app)

    def _make_running_job(self):
        from discovery_api.parser_router import _ClumpJob, _jobs
        from discovery_api.session_registry import SessionClump

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")
        clump.parser_client_list[0].health.mark_connected()
        _jobs["pid"] = _ClumpJob(clump=clump, parser_id="pid")
        return clump

    @patch("discovery_api.parser_router.enqueue_parser_add_channels", new_callable=AsyncMock)
    def test_async_add_channels_use_pg_queue(self, mock_enqueue: AsyncMock) -> None:
        from discovery_api.queue.producer import EnqueueAddChannelsResult

        async def _fake_enqueue(**kwargs):
            return EnqueueAddChannelsResult(
                task_ids=[101, 102],
                action_id=kwargs["action_id"],
            )

        mock_enqueue.side_effect = _fake_enqueue
        self._make_running_job()
        client = self._make_client()

        resp = client.post(
            "/discovery-api/parser/pid/add-channels",
            json={"channel_list": ["@a", "@b"]},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["async_mode"])
        self.assertEqual(len(body["action_id"]), 32)
        self.assertEqual(body["task_ids"], [101, 102])
        mock_enqueue.assert_awaited_once()
        call_kwargs = mock_enqueue.await_args.kwargs
        self.assertEqual(call_kwargs["parser_id"], "pid")
        self.assertEqual(call_kwargs["channel_list"], ["@a", "@b"])
        self.assertIn("webhook_url", call_kwargs)
        self.assertTrue(str(call_kwargs["webhook_url"]).startswith("http://h"))
        self.assertEqual(body["action_id"], call_kwargs["action_id"])

    @patch("discovery_api.parser_router.enqueue_parser_add_channels", new_callable=AsyncMock)
    def test_async_add_channels_pg_dedup_returns_existing_ids(
        self, mock_enqueue: AsyncMock
    ) -> None:
        from discovery_api.queue.producer import EnqueueAddChannelsResult

        mock_enqueue.return_value = EnqueueAddChannelsResult(
            task_ids=[55, 55],
            action_id="dedup-action",
        )
        self._make_running_job()
        client = self._make_client()

        resp = client.post(
            "/discovery-api/parser/pid/add-channels",
            json={"channel_list": ["@dup", "@dup"]},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["task_ids"], [55, 55])


class ProducerUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_one_task_per_channel(self) -> None:
        from app_balance.queue.accounts import AccountsRepo
        from app_balance.queue.source_channels import SourceChannelsRepo
        from app_balance.queue.task_queue import EnqueueInput, EnqueueResult, TaskQueueRepo
        from discovery_api.queue.producer import enqueue_parser_add_channels

        with patch.object(
            TaskQueueRepo, "enqueue_many", new_callable=AsyncMock
        ) as mock_enqueue_many, patch.object(
            SourceChannelsRepo, "find_ids_by_refs", new_callable=AsyncMock
        ) as mock_find, patch.object(
            AccountsRepo, "list_by_session_names", new_callable=AsyncMock, return_value={}
        ):
            mock_find.return_value = {"@a": 101, "@b": 102}
            mock_enqueue_many.return_value = [
                EnqueueResult(created=True, task_id=10),
                EnqueueResult(created=False, task_id=None, existing_task_id=11),
            ]

            result = await enqueue_parser_add_channels(
                parser_id="p1",
                channel_list=["@a", "@b"],
                webhook_url="http://wh",
                action_id="act-1",
            )

        self.assertEqual(result.task_ids, [10, 11])
        self.assertEqual(result.action_id, "act-1")
        mock_enqueue_many.assert_awaited_once()
        mock_find.assert_awaited_once_with(["@a", "@b"])

        items: list[EnqueueInput] = mock_enqueue_many.await_args.args[0]
        self.assertEqual(len(items), 2)
        first_call = items[0]
        self.assertEqual(first_call.task_type_code, "parser_add_channel")
        self.assertEqual(first_call.dedup_key, "parser_add_channel:p1:a")
        self.assertEqual(first_call.channel_id, 101)
        self.assertEqual(first_call.payload["parser_id"], "p1")
        self.assertEqual(first_call.payload["channel_ref"], "@a")
        self.assertEqual(first_call.payload["action_id"], "act-1")
        self.assertEqual(first_call.payload["webhook_url"], "http://wh")
        self.assertEqual(first_call.created_by, "discovery_api:add-channels")
        self.assertTrue(mock_enqueue_many.await_args.kwargs["skip_known_fatal"])

    async def test_enqueue_skips_channel_with_fatal_history(self) -> None:
        """B12: канал с фатальной историей не создаёт новую задачу."""
        from app_balance.queue.accounts import AccountsRepo
        from app_balance.queue.source_channels import SourceChannelsRepo
        from app_balance.queue.task_queue import EnqueueResult, TaskQueueRepo
        from discovery_api.queue.producer import enqueue_parser_add_channels

        with patch.object(
            TaskQueueRepo, "enqueue_many", new_callable=AsyncMock
        ) as mock_enqueue_many, patch.object(
            SourceChannelsRepo, "find_ids_by_refs", new_callable=AsyncMock
        ) as mock_find, patch.object(
            AccountsRepo, "list_by_session_names", new_callable=AsyncMock, return_value={}
        ):
            mock_find.return_value = {"@dead": 101, "@ok": 102}
            mock_enqueue_many.return_value = [
                EnqueueResult(
                    created=False,
                    task_id=None,
                    existing_task_id=42,
                    skipped_reason="fatal_history",
                    fatal_error_code="banned",
                ),
                EnqueueResult(created=True, task_id=10),
            ]

            result = await enqueue_parser_add_channels(
                parser_id="p1",
                channel_list=["@dead", "@ok"],
                action_id="act-3",
            )

        self.assertEqual(result.task_ids, [10])
        self.assertEqual(result.skipped_fatal, {"@dead": "banned"})
        self.assertTrue(mock_enqueue_many.await_args.kwargs["skip_known_fatal"])

    async def test_enqueue_force_retry_disables_fatal_skip(self) -> None:
        """B12: force_retry=True (skip_known_fatal=False) — ручной override."""
        from app_balance.queue.accounts import AccountsRepo
        from app_balance.queue.source_channels import SourceChannelsRepo
        from app_balance.queue.task_queue import EnqueueResult, TaskQueueRepo
        from discovery_api.queue.producer import enqueue_parser_add_channels

        with patch.object(
            TaskQueueRepo, "enqueue_many", new_callable=AsyncMock
        ) as mock_enqueue_many, patch.object(
            SourceChannelsRepo,
            "find_ids_by_refs",
            new_callable=AsyncMock,
            return_value={"@retry_me": 1},
        ), patch.object(
            AccountsRepo, "list_by_session_names", new_callable=AsyncMock, return_value={}
        ):
            mock_enqueue_many.return_value = [
                EnqueueResult(created=True, task_id=99)
            ]

            result = await enqueue_parser_add_channels(
                parser_id="p1",
                channel_list=["@retry_me"],
                action_id="act-4",
                skip_known_fatal=False,
            )

        self.assertEqual(result.task_ids, [99])
        self.assertEqual(result.skipped_fatal, {})
        self.assertFalse(mock_enqueue_many.await_args.kwargs["skip_known_fatal"])

    async def test_enqueue_skips_channel_on_active_clump_owner(self) -> None:
        """Канал уже слушается active+enabled аккаунтом clump → задача не ставится."""
        from app_balance.queue.accounts import Account, AccountsRepo
        from app_balance.queue.source_channels import SourceChannelsRepo
        from app_balance.queue.task_queue import EnqueueResult, TaskQueueRepo
        from discovery_api.session_registry import SessionClump
        from discovery_api.queue.producer import enqueue_parser_add_channels

        clump = SessionClump(["/s1", "/s2"], "c", webhook_url="http://h")
        clump.assignments["@busy"] = "/s2"

        active = Account(
            id=5,
            session_name="s2",
            status="active",
            is_enabled=True,
            current_task_id=None,
            cooldown_until=None,
            last_used_at=None,
        )

        with patch(
            "discovery_api.session_registry.get_clump", return_value=clump
        ), patch.object(
            AccountsRepo,
            "list_by_session_names",
            new_callable=AsyncMock,
            return_value={"s2": active},
        ), patch.object(
            TaskQueueRepo, "enqueue_many", new_callable=AsyncMock
        ) as mock_enqueue_many, patch.object(
            SourceChannelsRepo,
            "find_ids_by_refs",
            new_callable=AsyncMock,
            return_value={"@fresh": 1},
        ):
            mock_enqueue_many.return_value = [
                EnqueueResult(created=True, task_id=7)
            ]

            result = await enqueue_parser_add_channels(
                parser_id="p1",
                channel_list=["@busy", "@fresh"],
                action_id="act-clump",
            )

        self.assertEqual(result.task_ids, [7])
        self.assertEqual(result.skipped_in_clump, {"@busy": "/s2"})
        mock_enqueue_many.assert_awaited_once()
        items = mock_enqueue_many.await_args.args[0]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].payload["channel_ref"], "@fresh")

    async def test_enqueue_keeps_channel_when_owner_not_active(self) -> None:
        """Владелец в clump, но аккаунт disabled → задачу всё равно ставим."""
        from app_balance.queue.accounts import Account, AccountsRepo
        from app_balance.queue.source_channels import SourceChannelsRepo
        from app_balance.queue.task_queue import EnqueueResult, TaskQueueRepo
        from discovery_api.session_registry import SessionClump
        from discovery_api.queue.producer import enqueue_parser_add_channels

        clump = SessionClump(["/s1", "/s2"], "c", webhook_url="http://h")
        clump.assignments["@busy"] = "/s2"

        disabled = Account(
            id=5,
            session_name="s2",
            status="disabled",
            is_enabled=False,
            current_task_id=None,
            cooldown_until=None,
            last_used_at=None,
        )

        with patch(
            "discovery_api.session_registry.get_clump", return_value=clump
        ), patch.object(
            AccountsRepo,
            "list_by_session_names",
            new_callable=AsyncMock,
            return_value={"s2": disabled},
        ), patch.object(
            TaskQueueRepo, "enqueue_many", new_callable=AsyncMock
        ) as mock_enqueue_many, patch.object(
            SourceChannelsRepo,
            "find_ids_by_refs",
            new_callable=AsyncMock,
            return_value={"@busy": 1},
        ):
            mock_enqueue_many.return_value = [
                EnqueueResult(created=True, task_id=8)
            ]

            result = await enqueue_parser_add_channels(
                parser_id="p1",
                channel_list=["@busy"],
                action_id="act-disabled",
            )

        self.assertEqual(result.task_ids, [8])
        self.assertEqual(result.skipped_in_clump, {})
        mock_enqueue_many.assert_awaited_once()

    async def test_enqueue_skips_channel_already_queued_under_other_parser(
        self,
    ) -> None:
        """Регрессия: dedup_key = f"{type}:{parser_id}:{channel}" не ловит

        повторную постановку ОДНОГО И ТОГО ЖЕ канала под ДРУГИМ parser_id
        (напр. n8n tg-parser-sync на следующем тике выбирает другой target-
        парсер для ещё не подключённого канала). Раньше это плодило дубли-
        «близнецы» без ограничения — очередь росла на тысячи лишних задач
        для каналов без единого проекта (см. docs/discovery-vps104.local.md).
        Теперь channel_id-уровневый чек (find_active_task_ids_by_channel_ids)
        должен отсечь канал, у которого уже есть активная задача под другим
        parser_id, ДО того, как для него сформируется новый EnqueueInput.
        """
        from app_balance.queue.accounts import AccountsRepo
        from app_balance.queue.source_channels import SourceChannelsRepo
        from app_balance.queue.task_queue import EnqueueResult, TaskQueueRepo
        from discovery_api.queue.producer import enqueue_parser_add_channels

        with patch.object(
            TaskQueueRepo, "enqueue_many", new_callable=AsyncMock
        ) as mock_enqueue_many, patch.object(
            TaskQueueRepo,
            "find_active_task_ids_by_channel_ids",
            new_callable=AsyncMock,
        ) as mock_find_active, patch.object(
            SourceChannelsRepo,
            "find_ids_by_refs",
            new_callable=AsyncMock,
            return_value={"@already": 101, "@fresh": 102},
        ), patch.object(
            AccountsRepo, "list_by_session_names", new_callable=AsyncMock, return_value={}
        ):
            # @already (channel_id=101) уже стоит активной задачей id=555,
            # поставленной ранее под ДРУГИМ parser_id ("p0" != "p1").
            mock_find_active.return_value = {101: 555}
            mock_enqueue_many.return_value = [
                EnqueueResult(created=True, task_id=10),
            ]

            result = await enqueue_parser_add_channels(
                parser_id="p1",
                channel_list=["@already", "@fresh"],
                action_id="act-already-queued",
            )

        self.assertEqual(result.task_ids, [10])
        self.assertEqual(result.skipped_already_queued, {"@already": "555"})
        mock_find_active.assert_awaited_once()
        call_args = mock_find_active.await_args.args
        self.assertEqual(sorted(call_args[0]), [101, 102])
        self.assertEqual(call_args[1], "parser_add_channel")

        mock_enqueue_many.assert_awaited_once()
        items = mock_enqueue_many.await_args.args[0]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].payload["channel_ref"], "@fresh")

    async def test_enqueue_sets_account_id_to_clump_owner(self) -> None:
        """Регресс "Сессия не найдена в clump": без явного account_id задача

        уходит на auto-pick в dispatch.py, который берёт ЛЮБОЙ доступный
        аккаунт — почти никогда совпадающий с единственной сессией, реально
        держащей этот clump. enqueue_parser_add_channels должен резолвить
        владельца clump (session_name_list из одной сессии) в account_id,
        чтобы dispatch.py::_reserve_account пошёл по _reserve_fixed_account
        и зарезервировал именно его."""
        from app_balance.queue.accounts import AccountsRepo
        from app_balance.queue.source_channels import SourceChannelsRepo
        from app_balance.queue.task_queue import EnqueueResult, TaskQueueRepo
        from discovery_api.session_registry import SessionClump
        from discovery_api.queue.producer import enqueue_parser_add_channels

        clump = SessionClump(["/s1"], "c", webhook_url="http://h")

        with (
            patch(
                "discovery_api.session_registry.get_clump", return_value=clump
            ),
            patch.object(
                TaskQueueRepo, "enqueue_many", new_callable=AsyncMock
            ) as mock_enqueue_many,
            patch.object(
                SourceChannelsRepo,
                "find_ids_by_refs",
                new_callable=AsyncMock,
                return_value={"@fresh": 101},
            ),
            patch.object(
                AccountsRepo, "list_by_session_names", new_callable=AsyncMock, return_value={}
            ),
            patch.object(
                AccountsRepo,
                "get_id_by_session_name",
                new_callable=AsyncMock,
                return_value=777,
            ) as mock_get_id,
        ):
            mock_enqueue_many.return_value = [
                EnqueueResult(created=True, task_id=10)
            ]

            result = await enqueue_parser_add_channels(
                parser_id="p1",
                channel_list=["@fresh"],
                action_id="act-owner",
            )

        self.assertEqual(result.task_ids, [10])
        mock_get_id.assert_awaited_once_with("/s1")
        items = mock_enqueue_many.await_args.args[0]
        self.assertEqual(items[0].account_id, 777)

    async def test_enqueue_leaves_auto_pick_when_clump_has_multiple_sessions(
        self,
    ) -> None:
        """Несколько сессий в clump — нет единственного владельца, account_id

        не резолвим (остаётся auto-pick, как раньше)."""
        from app_balance.queue.accounts import AccountsRepo
        from app_balance.queue.source_channels import SourceChannelsRepo
        from app_balance.queue.task_queue import EnqueueResult, TaskQueueRepo
        from discovery_api.session_registry import SessionClump
        from discovery_api.queue.producer import enqueue_parser_add_channels

        clump = SessionClump(["/s1", "/s2"], "c", webhook_url="http://h")

        with (
            patch(
                "discovery_api.session_registry.get_clump", return_value=clump
            ),
            patch.object(
                TaskQueueRepo, "enqueue_many", new_callable=AsyncMock
            ) as mock_enqueue_many,
            patch.object(
                SourceChannelsRepo,
                "find_ids_by_refs",
                new_callable=AsyncMock,
                return_value={"@fresh": 101},
            ),
            patch.object(
                AccountsRepo, "list_by_session_names", new_callable=AsyncMock, return_value={}
            ),
            patch.object(
                AccountsRepo, "get_id_by_session_name", new_callable=AsyncMock
            ) as mock_get_id,
        ):
            mock_enqueue_many.return_value = [
                EnqueueResult(created=True, task_id=11)
            ]

            await enqueue_parser_add_channels(
                parser_id="p1",
                channel_list=["@fresh"],
                action_id="act-multi",
            )

        mock_get_id.assert_not_awaited()
        items = mock_enqueue_many.await_args.args[0]
        self.assertIsNone(items[0].account_id)

    async def test_enqueue_skips_empty_channels(self) -> None:
        from app_balance.queue.accounts import AccountsRepo
        from app_balance.queue.source_channels import SourceChannelsRepo
        from app_balance.queue.task_queue import EnqueueResult, TaskQueueRepo
        from discovery_api.queue.producer import enqueue_parser_add_channels

        with patch.object(
            TaskQueueRepo, "enqueue_many", new_callable=AsyncMock
        ) as mock_enqueue_many, patch.object(
            SourceChannelsRepo,
            "find_ids_by_refs",
            new_callable=AsyncMock,
            return_value={"@ok": 999},
        ), patch.object(
            AccountsRepo, "list_by_session_names", new_callable=AsyncMock, return_value={}
        ):
            mock_enqueue_many.return_value = [
                EnqueueResult(created=True, task_id=7)
            ]

            result = await enqueue_parser_add_channels(
                parser_id="p1",
                channel_list=["", "   ", "@ok"],
                action_id="act-2",
            )

        self.assertEqual(result.task_ids, [7])
        mock_enqueue_many.assert_awaited_once()
        items = mock_enqueue_many.await_args.args[0]
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
