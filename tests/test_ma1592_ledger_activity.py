"""MA1592 장부 편입 — 활동 로그(파일 공유) 테스트."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.auto_trade_activity_log import (
    log_ma1592_ledger_insert,
    merge_activity_events,
    read_ma1592_ledger_activity,
)


class Ma1592LedgerActivityTests(unittest.TestCase):
    def test_log_ma1592_ledger_insert_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            with patch("utils.auto_trade_activity_log._LEDGER_ACTIVITY_PATH", path):
                with patch("utils.auto_trade_activity_log.log_activity") as log_activity:
                    log_ma1592_ledger_insert(
                        "005930",
                        "삼성전자",
                        condition_label="1592매매",
                        insert_source="condition_realtime",
                    )
                    log_activity.assert_called_once()
                    rows = read_ma1592_ledger_activity(10)
            self.assertEqual(len(rows), 1)
            self.assertIn("장부 편입", rows[0]["message"])
            self.assertIn("삼성전자", rows[0]["message"])
            self.assertEqual(rows[0].get("strategy"), "ma1592")

    def test_merge_activity_events_dedupes(self):
        mem = [{
            "ts": "2026-08-28T15:00:00+09:00",
            "message": "[MA1592] 장부 편입: A(000001)",
            "stock_code": "000001",
            "source": "SCANNER",
        }]
        with patch(
            "utils.auto_trade_activity_log.read_ma1592_ledger_activity",
            return_value=[dict(mem[0])],
        ):
            merged = merge_activity_events(mem, 10)
        self.assertEqual(len(merged), 1)


if __name__ == "__main__":
    unittest.main()
