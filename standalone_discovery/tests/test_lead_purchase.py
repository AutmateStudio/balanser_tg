from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from discovery_api.lead_purchase import (
    protect_notification,
    register_purchase_callbacks,
    render,
)


class PurchaseCardTests(TestCase):
    def setUp(self):
        self.original = {
            "text": "Новый лид: @author",
            "buttons": [
                [
                    {
                        "text": "Открыть карточку",
                        "url": "https://leadochka.f5tech.ru/leads?leadId=42",
                    },
                    {"text": "Написать", "url": "https://t.me/author"},
                ]
            ],
        }
        self.preview = dict(
            text="Нужен дизайн",
            purchased=False,
            available=True,
            price=10,
            balance=100,
            projectId=1,
        )

    @patch("discovery_api.lead_purchase.load")
    def test_locked_replaces_original_text_and_links(self, load):
        load.return_value = self.preview
        card = protect_notification(17, self.original)
        self.assertNotIn("author", str(card))
        self.assertIn("Купить лид", str(card))
        self.assertIn("lead:quote:42", str(card))
        load.assert_called_once_with(17, 42)

    @patch("discovery_api.lead_purchase.load", side_effect=OSError("offline"))
    def test_failure_or_group_message_never_exposes_sender(self, load):
        for target in (17, -123):
            card = protect_notification(target, self.original)
            self.assertNotIn("author", str(card))
            self.assertNotIn("Написать", str(card))
        self.assertEqual(load.call_count, 1)

    @patch("discovery_api.lead_purchase.load")
    def test_private_callback_quotes_then_buys(self, load):
        bot = Mock()
        handlers = []
        bot.callback_query_handler.return_value = lambda fn: handlers.append(fn)
        register_purchase_callbacks(bot, lambda rows: rows)
        query = SimpleNamespace(
            id="test",
            from_user=SimpleNamespace(id=17),
            message=SimpleNamespace(
                chat=SimpleNamespace(type="private", id=17), message_id=5
            ),
        )
        load.return_value = self.preview
        query.data = "lead:quote:42"
        handlers[0](query)
        self.assertIn(
            "Подтвердить за 10 кредитов", str(bot.edit_message_text.call_args)
        )
        self.assertNotIn("Написать", str(bot.edit_message_text.call_args))
        load.return_value = dict(
            self.preview,
            purchased=True,
            authorUrl="https://t.me/author",
            channel="Источник",
        )
        query.data = "lead:buy:42:10"
        handlers[0](query)
        load.assert_called_with(17, 42, "buy", 10)
        self.assertIn("Написать автору", str(bot.edit_message_text.call_args))
        query.from_user.id = 31
        load.reset_mock()
        handlers[0](query)
        load.assert_not_called()

    def test_insufficient_funds_show_wallet_without_confirmation(self):
        card = render(
            42, dict(self.preview, balance=0, text="<b>Запрос</b>"), confirm=True
        )
        self.assertIn("Пополнить баланс", str(card["buttons"]))
        self.assertNotIn("lead:buy:", str(card["buttons"]))
        self.assertEqual(card["parse_mode"], "HTML")
        self.assertNotIn("<b>", card["text"])
