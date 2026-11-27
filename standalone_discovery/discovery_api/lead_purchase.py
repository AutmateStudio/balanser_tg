"""Приватная карточка лида. Оплата и проверка прав принадлежат кабинету."""

import hashlib
import hmac
from html import escape
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen


def site():
    return os.getenv("LIDOGEN_SITE_URL", "https://leadochka.f5tech.ru").rstrip("/")


def lead_id(message):
    for row in message.get("buttons") or []:
        for button in row if isinstance(row, list) else [row]:
            if not isinstance(button, dict):
                continue
            link = urlsplit(str(button.get("url", "")))
            if link.path.rstrip("/") != "/leads":
                continue
            value = parse_qs(link.query).get("leadId", [""])[0]
            if re.fullmatch(r"[1-9][0-9]{0,14}", value):
                return int(value)
    return None


def load(recipient, run_id, action="preview", price=None):
    secret = os.getenv("CLIENT_WEBHOOK_SECRET")
    if not secret:
        raise ValueError("Откройте карточку в кабинете")
    data = json.dumps(
        dict(recipient=str(recipient), runId=run_id, action=action, price=price)
    ).encode()
    signature = hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
    req = Request(
        site() + "/api/internal/telegram/lead-purchase",
        data=data,
        headers={"Content-Type": "application/json", "X-Telegram-Signature": signature},
    )
    try:
        with urlopen(req, timeout=8) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            reason = json.load(exc).get("error")
        except (ValueError, AttributeError):
            reason = None
        raise ValueError(reason or "Откройте карточку в кабинете") from exc


def card_url(run_id):
    return site() + "/leads?leadId=" + str(run_id)


def render(run_id, card, confirm=False):
    text = card["text"]
    buttons = []
    if card["purchased"]:
        text += "\n\nКуплен · " + (card.get("channel") or "")
        url = card.get("authorUrl")
        if url and urlsplit(url).scheme == "https":
            buttons.append([{"text": "Написать автору", "url": url}])
    elif card["available"] and card.get("canSpend") is True:
        price = card["price"]
        text += f"\n\nСтоимость: {price} кредитов"
        if confirm:
            text += f"\nБаланс: {card['balance']} кредитов"
            if card["balance"] < price and card.get("canTopUp") is True:
                buttons.append(
                    [
                        {
                            "text": "Пополнить баланс",
                            "url": site()
                            + "/wallet?projectId="
                            + str(card["projectId"]),
                        }
                    ]
                )
            elif card["balance"] >= price:
                buttons.append(
                    [
                        {
                            "text": f"Подтвердить за {price} кредитов",
                            "callback_data": f"lead:buy:{run_id}:{price}",
                        }
                    ]
                )
            buttons.append([{"text": "Отмена", "callback_data": f"lead:view:{run_id}"}])
        else:
            buttons.append(
                [{"text": "Купить лид", "callback_data": f"lead:quote:{run_id}"}]
            )
    buttons.append([{"text": "Открыть карточку на сайте", "url": card_url(run_id)}])
    return {
        "text": escape(text),
        "layout": "inline",
        "buttons": buttons,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }


def protect_notification(recipient, message):
    run_id = lead_id(message)
    if not run_id:
        if re.search(r"(?i)(принят|новый|найден) лид", str(message.get("text", ""))):
            return {
                "text": "Новый лид в кабинете",
                "buttons": [[{"text": "Открыть лиды", "url": site() + "/leads"}]],
            }
        return message
    fallback = {
        "text": "Новый лид в кабинете",
        "buttons": [[{"text": "Открыть карточку на сайте", "url": card_url(run_id)}]],
    }
    if not re.fullmatch(r"[1-9][0-9]{0,18}", str(recipient)):
        return fallback
    try:
        return render(run_id, load(recipient, run_id))
    except (ValueError, OSError, KeyError):
        return fallback


def register_purchase_callbacks(bot, markup):
    @bot.callback_query_handler(
        func=lambda q: bool(q.data and q.data.startswith("lead:"))
    )
    def purchase(q):
        match = re.fullmatch(
            r"lead:(view|quote|buy):([1-9][0-9]{0,14})(?::([1-9][0-9]{0,8}))?", q.data
        )
        if (
            not match
            or not q.message
            or q.message.chat.type != "private"
            or q.message.chat.id != q.from_user.id
        ):
            bot.answer_callback_query(q.id, "Откройте личный чат с ботом")
            return
        action, raw_id, price = match.groups()
        try:
            run_id = int(raw_id)
            data = load(
                q.from_user.id,
                run_id,
                "buy" if action == "buy" else "preview",
                int(price) if price else None,
            )
            card = render(run_id, data, confirm=action == "quote")
            bot.edit_message_text(
                card["text"],
                q.message.chat.id,
                q.message.message_id,
                parse_mode=card["parse_mode"],
                disable_web_page_preview=True,
                reply_markup=markup(card["buttons"]),
            )
            bot.answer_callback_query(q.id)
        except (ValueError, OSError, KeyError) as exc:
            bot.answer_callback_query(
                q.id,
                str(exc)[:160]
                if isinstance(exc, ValueError)
                else "Не удалось обновить карточку",
                show_alert=True,
            )
