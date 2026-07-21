import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from utils import stock_news_progress as snp


class StockNewsProgressDayRolloverTests(unittest.TestCase):
    def test_stale_all_done_file_becomes_pending_for_today(self):
        today = date(2026, 7, 21)
        yesterday = today - timedelta(days=1)
        with tempfile.TemporaryDirectory() as td:
            progress_path = os.path.join(td, "_stock_news_progress.json")
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "biz_date": yesterday.isoformat(),
                        "running": False,
                        "status": "all_done",
                        "universe_total": 3941,
                        "done_count": 3941,
                        "pending_count": 0,
                        "percent": 100.0,
                    },
                    f,
                )
            with mock.patch.object(snp, "PROGRESS_FILE", progress_path), mock.patch.object(
                snp, "STOCK_NEWS_LOG", os.path.join(td, "missing.log")
            ), mock.patch.object(snp, "kst_today", return_value=today):
                out = snp.get_stock_news_progress(session=None)
        self.assertEqual(out["biz_date"], today.isoformat())
        self.assertTrue(out["needs_new_day_run"])
        self.assertEqual(out["pending_count"], 3941)
        self.assertEqual(out["done_count"], 0)
        self.assertNotEqual(out["status"], "all_done")

    def test_today_all_done_stays_done(self):
        today = date(2026, 7, 21)
        with tempfile.TemporaryDirectory() as td:
            progress_path = os.path.join(td, "_stock_news_progress.json")
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "biz_date": today.isoformat(),
                        "running": False,
                        "status": "all_done",
                        "universe_total": 100,
                        "done_count": 100,
                        "pending_count": 0,
                    },
                    f,
                )
            with mock.patch.object(snp, "PROGRESS_FILE", progress_path), mock.patch.object(
                snp, "STOCK_NEWS_LOG", os.path.join(td, "missing.log")
            ), mock.patch.object(snp, "kst_today", return_value=today):
                out = snp.get_stock_news_progress(session=None)
        self.assertEqual(out["status"], "all_done")
        self.assertEqual(out["pending_count"], 0)
        self.assertFalse(out["needs_new_day_run"])


class ContinueStopLogicTests(unittest.TestCase):
    def test_should_not_stop_on_new_day(self):
        from scripts import continue_stock_news_batch as cont

        progress = {
            "biz_date": "2026-07-21",
            "progress_file_biz_date": "2026-07-10",
            "status": "pending",
            "pending_count": 3941,
            "needs_new_day_run": True,
        }
        self.assertFalse(cont._should_stop(progress, "2026-07-21"))

    def test_should_stop_when_today_complete(self):
        from scripts import continue_stock_news_batch as cont

        progress = {
            "biz_date": "2026-07-21",
            "progress_file_biz_date": "2026-07-21",
            "status": "all_done",
            "pending_count": 0,
            "done_count": 3941,
            "universe_total": 3941,
            "needs_new_day_run": False,
        }
        self.assertTrue(cont._should_stop(progress, "2026-07-21"))


if __name__ == "__main__":
    unittest.main()
