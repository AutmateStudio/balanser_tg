from __future__ import annotations

from typing import Any

from telebot import types

from discovery_api.bot_registry import get_bot


def _normalize_button_rows(buttons: Any) -> list[list[dict[str, Any]]]:
    """Принимает кнопки как плоский список или список рядов и возвращает ряды."""
    if not buttons:
        return []
    if not isinstance(buttons, list):
        raise ValueError("buttons должен быть списком")

    first_item = buttons[0] if buttons else None
    if isinstance(first_item, dict):
        return [buttons]
    if all(isinstance(row, list) for row in buttons):
        return buttons

    raise ValueError("buttons должен быть списком кнопок или списком рядов кнопок")


def _build_inline_markup(buttons: Any) -> types.InlineKeyboardMarkup | None:
    rows = _normalize_button_rows(buttons)
    if not rows:
        return None

    markup = types.InlineKeyboardMarkup()
    for row in rows:
        markup_row = []
        for button in row:
            text = button.get("text")
            url = button.get("url")
            callback_data = button.get("callback_data")

            if not text:
                raise ValueError("У каждой inline-кнопки должен быть text")
            if url:
                markup_row.append(types.InlineKeyboardButton(text=text, url=url))
            elif callback_data:
                markup_row.append(
                    types.InlineKeyboardButton(
                        text=text,
                        callback_data=callback_data,
                    )
                )
            else:
                raise ValueError(
                    "У каждой inline-кнопки должен быть url или callback_data"
                )

        if markup_row:
            markup.row(*markup_row)

    return markup


def _build_keyboard_markup(buttons: Any) -> types.ReplyKeyboardMarkup | None:
    rows = _normalize_button_rows(buttons)
    if not rows:
        return None

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for row in rows:
        markup_row = []
        for button in row:
            text = button.get("text")
            if not text:
                raise ValueError("У каждой keyboard-кнопки должен быть text")
            markup_row.append(types.KeyboardButton(text=text))

        if markup_row:
            markup.row(*markup_row)

    return markup


def _build_reply_markup(layout: str | None, buttons: Any) -> Any:
    if not buttons:
        return None

    normalized_layout = (layout or "inline").strip().lower()
    if normalized_layout == "inline":
        return _build_inline_markup(buttons)
    if normalized_layout == "keyboard":
        return _build_keyboard_markup(buttons)

    raise ValueError("layout должен быть 'inline' или 'keyboard'")


def send_bot_message(chat_id: int, message: dict[str, Any]) -> Any:
    bot = get_bot()

    target_chat_id = chat_id or message.get("chat_id")
    text = message.get("text") or ""
    image_url = message.get("image_url")
    layout = message.get("layout")
    buttons = message.get("buttons", [])
    parse_mode = message.get("parse_mode", "HTML")
    disable_web_page_preview = message.get("disable_web_page_preview", True)

    if not target_chat_id:
        raise ValueError("chat_id обязателен")
    if not text and not image_url:
        raise ValueError("Нужно передать text или image_url")

    reply_markup = _build_reply_markup(layout, buttons)

    if image_url:
        return bot.send_photo(
            chat_id=target_chat_id,
            photo=image_url,
            caption=text or None,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    return bot.send_message(
        chat_id=target_chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )