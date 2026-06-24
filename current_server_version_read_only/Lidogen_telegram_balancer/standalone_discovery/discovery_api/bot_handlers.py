from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
import telebot

from discovery_api.config import get_start_webhook_url

log = logging.getLogger(__name__)

_handlers_registered = False


def _message_to_dict(message: telebot.types.Message) -> dict[str, Any]:
    raw_json = getattr(message, "json", None)
    if isinstance(raw_json, dict):
        return raw_json

    to_dict = getattr(message, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if isinstance(data, dict):
            return data

    to_json = getattr(message, "to_json", None)
    if callable(to_json):
        data = json.loads(to_json())
        if isinstance(data, dict):
            return data

    return {
        "message_id": getattr(message, "message_id", None),
        "date": getattr(message, "date", None),
        "text": getattr(message, "text", None),
        "chat": getattr(getattr(message, "chat", None), "id", None),
        "from_user": getattr(getattr(message, "from_user", None), "id", None),
    }


def _extract_start_payload(text: str | None) -> str:
    parts = (text or "").split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


async def _post_start_webhook(message: telebot.types.Message) -> None:
    webhook_url = get_start_webhook_url()
    payload = {
        "event": "telegram_bot_start",
        "start_payload": _extract_start_payload(message.text),
        "message": _message_to_dict(message),
    }

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(webhook_url, json=payload) as response:
            response.raise_for_status()


def _run_async(coro: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return

    loop.create_task(coro)


def register_bot_handlers(bot: telebot.TeleBot) -> None:
    global _handlers_registered

    if _handlers_registered:
        return

    @bot.message_handler(commands=["start"])
    def handle_start(message: telebot.types.Message) -> None:
        try:
            _run_async(_post_start_webhook(message))
        except Exception:
            log.exception("Ошибка отправки /start-события на START_WEBHOOK_URL")

    _handlers_registered = True
