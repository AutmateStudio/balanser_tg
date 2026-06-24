"""Юнит-тесты для эндпойнтов управления списком каналов парсера.

Проверяем:
- `_normalize_channel_ref` корректно срезает `t.me/`, `@`, пробелы;
- `resolve_channel_to_chat_id` отдаёт числовой id из кеша и из мок-клиента;
- три новых эндпойнта работают через FastAPI TestClient с подменой
  `_jobs[parser_id]` на job с мок-клиентом.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from discovery_api.parser_functions import (
    _normalize_channel_ref,
    resolve_channel_to_chat_id,
)
from discovery_api.parser_router import _ClumpJob, _jobs, parser_router
from discovery_api.session_registry import SessionClump


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class NormalizeChannelRefTests(unittest.TestCase):
    def test_strip_https(self) -> None:
        self.assertEqual(_normalize_channel_ref("https://t.me/durov"), "durov")

    def test_strip_at(self) -> None:
        self.assertEqual(_normalize_channel_ref(" @durov "), "durov")

    def test_numeric_id(self) -> None:
        self.assertEqual(_normalize_channel_ref("-1001234567890"), "-1001234567890")

    def test_empty(self) -> None:
        self.assertEqual(_normalize_channel_ref(""), "")
        self.assertEqual(_normalize_channel_ref("   "), "")
        self.assertEqual(_normalize_channel_ref("@"), "")

    def test_message_link_strips_msg_id(self) -> None:
        self.assertEqual(
            _normalize_channel_ref("https://t.me/sutki_chat/716983"),
            "sutki_chat",
        )
        self.assertEqual(
            _normalize_channel_ref("t.me/balichat/3452435"), "balichat"
        )

    def test_query_and_fragment_are_stripped(self) -> None:
        self.assertEqual(
            _normalize_channel_ref("https://t.me/durov?single"), "durov"
        )
        self.assertEqual(
            _normalize_channel_ref("https://t.me/durov/100?single"), "durov"
        )
        self.assertEqual(
            _normalize_channel_ref("https://t.me/durov#anchor"), "durov"
        )

    def test_private_channel_link_returns_negative_chat_id(self) -> None:
        self.assertEqual(
            _normalize_channel_ref("https://t.me/c/2086716036/123"),
            "-1002086716036",
        )
        self.assertEqual(
            _normalize_channel_ref("https://t.me/c/2086716036"),
            "-1002086716036",
        )

    def test_invite_link_kept_as_is(self) -> None:
        self.assertEqual(
            _normalize_channel_ref("https://t.me/joinchat/AAAAAEhash"),
            "joinchat/AAAAAEhash",
        )
        self.assertEqual(
            _normalize_channel_ref("https://t.me/+AAAAAEhash"), "+AAAAAEhash"
        )


class ResolveChannelToChatIdTests(unittest.TestCase):
    def test_numeric_resolves_listen_target(self) -> None:
        from discovery_api.chat_resolve import ListenTarget

        target = ListenTarget(
            source_entity=MagicMock(),
            listen_entity=MagicMock(),
            source_peer_id=-1001234567890,
            listen_peer_id=-100999,
            entity_kind="channel",
            listen_mode="discussion",
            linked_chat_id=999,
            title="T",
            username=None,
        )
        client = MagicMock()
        client.is_connected.return_value = True

        with patch(
            "discovery_api.parser_functions.resolve_listen_target",
            new_callable=AsyncMock,
            return_value=target,
        ):
            chat_id, err = _run(resolve_channel_to_chat_id(client, "-1001234567890"))

        self.assertEqual(chat_id, -100999)
        self.assertIsNone(err)

    def test_empty_returns_error(self) -> None:
        client = MagicMock()
        chat_id, err = _run(resolve_channel_to_chat_id(client, "  "))
        self.assertIsNone(chat_id)
        self.assertIsNotNone(err)

    def test_disconnected_client_returns_error(self) -> None:
        client = MagicMock()
        client.is_connected.return_value = False
        chat_id, err = _run(resolve_channel_to_chat_id(client, "@durov"))
        self.assertIsNone(chat_id)
        assert err is not None
        self.assertIn("не подключ", err)


def _make_job(running: bool = True, parser_id: str = "pid") -> _ClumpJob:
    clump = SessionClump(
        ["/app/sessions/Test"],
        "test-clump",
        webhook_url="https://example.com/hook",
    )
    pc = clump.parser_client_list[0]
    pc.channels = ["@old"]
    pc.allowed_chat_ids = {-1001}
    pc.ref_to_chat_id = {"@old": -1001}
    clump.assignments = {"@old": pc.session_name}
    if running:
        task = MagicMock()
        task.done.return_value = False
        pc._supervisor_task = task  # type: ignore[assignment]
    job = _ClumpJob(clump=clump, parser_id=parser_id)
    if not running:
        job.finished = True
    return job


class ParserChannelsEndpointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app = FastAPI()
        app.include_router(parser_router)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        _jobs.clear()

    def setUp(self) -> None:
        # Не писать parser_jobs.json в реальный data/ при вызовах add/remove.
        os.environ["PARSER_PERSISTENCE_ENABLED"] = "0"

    def tearDown(self) -> None:
        _jobs.clear()
        os.environ.pop("PARSER_PERSISTENCE_ENABLED", None)

    def test_404_for_unknown_parser_on_list(self) -> None:
        resp = self.client.get("/discovery-api/parser/nope/channels")
        self.assertEqual(resp.status_code, 404)

    def test_409_when_clump_finished(self) -> None:
        job = _make_job(running=False, parser_id="fid")
        _jobs["fid"] = job
        resp = self.client.post(
            "/discovery-api/parser/fid/add-channels?async=0",
            json={"channel_list": ["@x"]},
        )
        self.assertEqual(resp.status_code, 409)

    def test_list_returns_current_state(self) -> None:
        job = _make_job()
        _jobs["pid"] = job

        resp = self.client.get("/discovery-api/parser/pid/channels")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["parser_id"], "pid")
        self.assertEqual(body["channel_list"], ["@old"])
        self.assertEqual(body["allowed_chat_ids"], [-1001])

    def test_add_numeric_chat_id(self) -> None:
        job = _make_job()
        _jobs["pid"] = job

        async def _fake_resolve(client, raw: str):
            if raw == "-100777":
                return -100222, None
            if raw == "-1001":
                return -1001, None
            return None, "ошибка"

        with patch(
            "discovery_api.session_registry.get_or_create_client",
            new_callable=AsyncMock,
            return_value=MagicMock(is_connected=MagicMock(return_value=True)),
        ), patch(
            "discovery_api.parser_functions.resolve_channel_to_chat_id",
            new=_fake_resolve,
        ), patch.object(job.clump, "start", new_callable=AsyncMock):
            resp = self.client.post(
                "/discovery-api/parser/pid/add-channels?async=0",
                json={"channel_list": ["-100777", "-1001"]},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["added"], ["-100777"])
        self.assertEqual(body["already_present"], ["-1001"])
        self.assertEqual(body["errors"], [])
        pc = job.clump.parser_client_list[0]
        self.assertIn(-100222, pc.allowed_chat_ids)
        self.assertIn("-100777", pc.channels)

    def test_add_username_uses_listen_resolve(self) -> None:
        from discovery_api.chat_resolve import ListenTarget
        from discovery_api import parser_functions as pf

        target = ListenTarget(
            source_entity=MagicMock(),
            listen_entity=MagicMock(),
            source_peer_id=-100111,
            listen_peer_id=-1002222,
            entity_kind="channel",
            listen_mode="discussion",
            linked_chat_id=222,
            title="New",
            username="newchan",
        )
        job = _make_job()
        _jobs["pid"] = job

        with patch(
            "discovery_api.session_registry.get_or_create_client",
            new_callable=AsyncMock,
            return_value=MagicMock(is_connected=MagicMock(return_value=True)),
        ), patch.object(pf, "get_cached_chat_ids", return_value={}), \
            patch.object(pf, "set_cached_chat_id", return_value=None), \
            patch.object(
                pf, "resolve_listen_target", new_callable=AsyncMock, return_value=target
            ), patch.object(job.clump, "start", new_callable=AsyncMock):
            resp = self.client.post(
                "/discovery-api/parser/pid/add-channels?async=0",
                json={"channel_list": ["@newchan"]},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["added"], ["@newchan"])
        self.assertIn(-1002222, job.clump.parser_client_list[0].allowed_chat_ids)

    def test_remove_numeric(self) -> None:
        job = _make_job()
        pc = job.clump.parser_client_list[0]
        pc.allowed_chat_ids.add(-100777)
        pc.channels.append("-100777")
        pc.ref_to_chat_id["-100777"] = -100777
        job.clump.assignments["-100777"] = pc.session_name
        _jobs["pid"] = job

        async def _fake_resolve(client, raw: str):
            if raw == "-100777":
                return -100777, None
            if raw == "-100999":
                return -100999, None
            return None, "ошибка"

        with patch(
            "discovery_api.session_registry.get_or_create_client",
            new_callable=AsyncMock,
            return_value=MagicMock(is_connected=MagicMock(return_value=True)),
        ), patch(
            "discovery_api.parser_functions.resolve_channel_to_chat_id",
            new=_fake_resolve,
        ):
            resp = self.client.post(
                "/discovery-api/parser/pid/remove-channels",
                json={"channel_list": ["-100777", "-100999"]},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["removed"], ["-100777"])
        self.assertEqual(body["not_found"], ["-100999"])
        self.assertNotIn(-100777, pc.allowed_chat_ids)
        self.assertNotIn("-100777", pc.channels)


if __name__ == "__main__":
    unittest.main()
