import json
import os
import tempfile
import unittest
from unittest import mock

from utils import tray_notify as tn


class TrayNotifyTests(unittest.TestCase):
    def test_enqueue_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "_tray_notify.jsonl")
            with mock.patch.object(tn, "TRAY_NOTIFY_FILE", path):
                ok = tn.enqueue_tray_notify(title="Stocke · 매수", body="테스트 1주", kind="info")
            self.assertTrue(ok)
            lines = open(path, encoding="utf-8").read().strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["title"], "Stocke · 매수")
            self.assertIn("테스트", row["body"])
            self.assertEqual(row["icon"], "info")

    def test_buy_sell_helpers(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "_tray_notify.jsonl")
            with mock.patch.object(tn, "TRAY_NOTIFY_FILE", path):
                tn.enqueue_trade_buy_tray(
                    stock_name="테스트",
                    stock_code="000000",
                    quantity=2,
                    price=10000,
                    is_add_buy=False,
                    strategy="legacy",
                )
                tn.enqueue_trade_sell_tray(
                    stock_name="테스트",
                    stock_code="000000",
                    quantity=2,
                    sell_price=11000,
                    sell_reason="TRAILING",
                    profit_loss=2000,
                    profit_loss_rate=10.0,
                )
            lines = open(path, encoding="utf-8").read().strip().splitlines()
            self.assertEqual(len(lines), 2)
            buy = json.loads(lines[0])
            sell = json.loads(lines[1])
            self.assertIn("신규매수", buy["title"])
            self.assertIn("익절", sell["title"])


if __name__ == "__main__":
    unittest.main()
