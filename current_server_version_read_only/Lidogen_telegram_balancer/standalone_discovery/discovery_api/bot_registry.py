from __future__ import annotations

import logging
import threading

import telebot

from discovery_api.config import get_bot_token

log = logging.getLogger(__name__)

_bot: telebot.TeleBot | None = None
_polling_started = False
_polling_thread: threading.Thread | None = None


def get_bot() -> telebot.TeleBot:
    global _bot
    if _bot is None:
        _bot = telebot.TeleBot(
            get_bot_token(),
            parse_mode="HTML",
        )
    return _bot


def start_bot_polling_once() -> None:
    """Запускает polling бота один раз в фоновом daemon-потоке."""
    global _polling_started, _polling_thread

    if _polling_started:
        return

    bot = get_bot()
    from discovery_api.bot_handlers import register_bot_handlers

    register_bot_handlers(bot)
    _polling_thread = threading.Thread(
        target=bot.infinity_polling,
        kwargs={"skip_pending": True},
        daemon=True,
        name="telegram-bot-polling",
    )
    _polling_thread.start()
    _polling_started = True
    log.info("Telegram bot polling запущен")


def stop_bot_polling() -> None:
    """Останавливает polling бота при shutdown приложения."""
    global _polling_started, _polling_thread

    if _bot is not None and _polling_started:
        _bot.stop_polling()
        log.info("Telegram bot polling остановлен")

    _polling_started = False
    _polling_thread = None


def reset_bot_for_tests() -> None:
    global _bot, _polling_started, _polling_thread
    if _bot is not None and _polling_started:
        _bot.stop_polling()
    _bot = None
    _polling_started = False
    _polling_thread = None