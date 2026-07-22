"""Unit-тесты webhook delivery (HTTP status + retries)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from discovery_api import parser_functions as pf


@pytest.mark.asyncio
async def test_send_message_raises_on_http_error() -> None:
    sender = object.__new__(pf.AsyncSender)
    sender.webhook_url = "https://example.com/hook"
    sender.api_key = None

    response = MagicMock()
    response.status = 500
    response.headers = {"Content-Type": "application/json"}
    response.request_info = MagicMock()
    response.history = ()
    response.json = AsyncMock(return_value={"ok": False})
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=response)
    sender.session = session

    with pytest.raises(Exception):
        await sender.send_message({"text": "hi"})


@pytest.mark.asyncio
async def test_dispatch_worker_retries_then_fails() -> None:
    pf._stats["delivered"] = 0
    pf._stats["retried"] = 0
    pf._stats["failed"] = 0
    pf._stats["webhook_errors"] = 0
    pf._WEBHOOK_MAX_ATTEMPTS = 3
    pf._WEBHOOK_BACKOFF_BASE_SECONDS = 0.01

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put({"webhook_url": "https://example.com/h", "chat_id": 1})
    pf._message_queue = queue

    with patch.object(
        pf,
        "send_message_to_webhook",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        task = asyncio.create_task(pf._dispatch_queue_worker())
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert pf._stats["failed"] >= 1
    assert pf._stats["retried"] >= 1
    assert pf._stats["delivered"] == 0
    pf._message_queue = None
