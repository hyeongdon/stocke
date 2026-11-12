import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import TelegramPriceAlert
from utils import telegram_price_alerts


class TelegramPriceAlertStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        TelegramPriceAlert.__table__.create(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, future=True)

    def tearDown(self):
        self.engine.dispose()

    def test_create_normalizes_code_and_delete_removes_alert(self):
        with patch.object(telegram_price_alerts, "SessionLocal", self.session_factory):
            alert = telegram_price_alerts.create_price_alert("A10140", 25_000)

            self.assertEqual(alert["stock_code"], "010140")
            self.assertEqual(alert["target_price"], 25_000)
            self.assertEqual(alert["status"], "active")
            self.assertEqual(len(telegram_price_alerts.list_price_alerts()), 1)

            self.assertTrue(telegram_price_alerts.delete_price_alert(alert["id"]))
            self.assertEqual(telegram_price_alerts.list_price_alerts(), [])

    def test_rejects_invalid_stock_code_before_database_write(self):
        with self.assertRaisesRegex(ValueError, "종목코드"):
            telegram_price_alerts.create_price_alert("samsung", 25_000)


class TelegramPriceAlertSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_notifies_once_when_price_is_at_or_below_target(self):
        class Api:
            async def get_current_price(self, stock_code):
                self.stock_code = stock_code
                return 9_900

        class Notifier:
            def __init__(self):
                self.messages = []

            def is_configured(self):
                return True

            def send_message(self, message, *, notification_key):
                self.messages.append((message, notification_key))
                return True

        api = Api()
        notifier = Notifier()
        active = [{"id": 17, "stock_code": "010140", "target_price": 10_000}]
        with (
            patch.object(telegram_price_alerts, "is_krx_session", return_value=True),
            patch.object(telegram_price_alerts, "_active_alerts", return_value=active),
            patch.object(telegram_price_alerts, "_record_check") as record_check,
            patch.object(telegram_price_alerts, "_mark_triggered") as mark_triggered,
        ):
            scheduler = telegram_price_alerts.TelegramPriceAlertScheduler(api, notifier)
            await scheduler.poll_once()

        self.assertEqual(api.stock_code, "010140")
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("9,900원", notifier.messages[0][0])
        self.assertIn("10,000원 이하", notifier.messages[0][0])
        self.assertEqual(notifier.messages[0][1], "price.alert.17")
        record_check.assert_called_once_with(17, 9_900)
        mark_triggered.assert_called_once_with(17)

    async def test_does_not_notify_when_price_is_above_target_or_market_closed(self):
        class Api:
            async def get_current_price(self, stock_code):
                return 10_100

        class Notifier:
            def __init__(self):
                self.messages = []

            def is_configured(self):
                return True

            def send_message(self, message, *, notification_key):
                self.messages.append(message)
                return True

        notifier = Notifier()
        active = [{"id": 5, "stock_code": "010140", "target_price": 10_000}]
        scheduler = telegram_price_alerts.TelegramPriceAlertScheduler(Api(), notifier)
        with (
            patch.object(telegram_price_alerts, "is_krx_session", return_value=False),
            patch.object(telegram_price_alerts, "_active_alerts", return_value=active) as get_active,
        ):
            await scheduler.poll_once()
        get_active.assert_not_called()
        self.assertEqual(notifier.messages, [])

        with (
            patch.object(telegram_price_alerts, "is_krx_session", return_value=True),
            patch.object(telegram_price_alerts, "_active_alerts", return_value=active),
            patch.object(telegram_price_alerts, "_record_check"),
            patch.object(telegram_price_alerts, "_mark_triggered") as mark_triggered,
        ):
            await scheduler.poll_once()
        self.assertEqual(notifier.messages, [])
        mark_triggered.assert_not_called()
