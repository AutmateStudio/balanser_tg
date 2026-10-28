"""Проверка сохранённого с VPS backfill без Telegram, Redis и production-БД."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from discovery_api import parser_router as module
from discovery_api.parser_functions import is_duplicate_webhook_rejection


def request(since):
    return module.ParserBackfillRequest(channels=[{"chat_id": -1001, "since": since, "limit": 2}])


@pytest.mark.asyncio
async def test_backfill_requires_enabled_durable_ingress(monkeypatch):
    monkeypatch.setattr(module, "redis_ingress_enabled", lambda: False)
    with pytest.raises(HTTPException) as error:
        await module.backfill_messages(request(datetime.now(timezone.utc)))
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_backfill_rejects_ambiguous_naive_timestamp(monkeypatch):
    monkeypatch.setattr(module, "redis_ingress_enabled", lambda: True)
    with pytest.raises(HTTPException) as error:
        await module.backfill_messages(request(datetime(2026, 1, 1)))
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_backfill_preserves_cursor_and_skips_empty_events(monkeypatch):
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)

    class Client:
        async def iter_messages(self, ref, *, limit, offset_id):
            assert ref == "@test_channel" and limit == 2 and offset_id == 0
            yield SimpleNamespace(id=12, message="test event", date=since, sender_id=7)
            yield SimpleNamespace(id=11, message="", date=since, sender_id=8)

    listener = SimpleNamespace(is_running=lambda: True, allowed_chat_ids={-1001},
                               ref_to_chat_id={"@test_channel": -1001}, get_client=AsyncMock(return_value=Client()))
    monkeypatch.setattr(module, "redis_ingress_enabled", lambda: True)
    monkeypatch.setattr(module, "_clump_is_running", lambda _: True)
    monkeypatch.setattr(module, "_jobs", {"test": SimpleNamespace(clump=SimpleNamespace(parser_client_list=[listener]))})
    publish = AsyncMock()
    monkeypatch.setattr(module, "publish_to_redis", publish)

    result = (await module.backfill_messages(request(since)))["results"][0]
    assert result["published"] == 1 and result["skipped_empty"] == 1
    assert result["next_offset_id"] == 11
    publish.assert_awaited_once()
    assert publish.await_args.args[0]["telegram_message"]["id"] == 12


def test_duplicate_downstream_rejection_is_recognized():
    assert is_duplicate_webhook_rejection('duplicate key value violates unique constraint')
    assert is_duplicate_webhook_rejection('uq_source_messages_channel_external')
    assert not is_duplicate_webhook_rejection('temporary upstream timeout')
