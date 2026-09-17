"""로그 7일 보관 정리."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from utils.datetime_kst import KST
from utils.log_cleanup import (
    cleanup_logs,
    parse_log_line_ts,
    format_bytes,
)


class LogCleanupTests(unittest.TestCase):
    def test_parse_common_prefixes(self):
        ts = parse_log_line_ts("2026-09-17 08:51:06,914 - managers.auto_trade_scanner - INFO")
        self.assertEqual(ts.day, 17)
        self.assertEqual(ts.hour, 8)
        ts2 = parse_log_line_ts("[2026-09-17 09:44:01] === 서버 재시작")
        self.assertEqual(ts2.hour, 9)
        ts3 = parse_log_line_ts("===== 2026-09-17 16:00:02.94 실행 시작")
        self.assertEqual(ts3.hour, 16)
        self.assertIsNone(parse_log_line_ts("no timestamp here"))

    def test_trims_old_lines_keeps_recent_and_continuations(self):
        now = datetime(2026, 9, 17, 19, 0, tzinfo=KST)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir()
            old = (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
            new = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
            target = logs / "app.log"
            target.write_text(
                f"{old} old line\ncontinuation of old\n{new} new line\ncontinuation of new\n",
                encoding="utf-8",
            )
            (logs / "_ma1592_universe.json").write_text("{}", encoding="utf-8")
            (root / "stock_pipeline.log").write_text(
                f"{old} pipeline old\n{new} pipeline new\n",
                encoding="utf-8",
            )
            summary = cleanup_logs(root, keep_days=7, now=now)
            kept = target.read_text(encoding="utf-8")
            self.assertNotIn("old line", kept)
            self.assertIn("new line", kept)
            self.assertIn("continuation of new", kept)
            self.assertNotIn("continuation of old", kept)
            pipe = (root / "stock_pipeline.log").read_text(encoding="utf-8")
            self.assertIn("pipeline new", pipe)
            self.assertNotIn("pipeline old", pipe)
            self.assertTrue((logs / "_ma1592_universe.json").exists())
            self.assertGreater(summary.freed_bytes, 0)

    def test_deletes_old_untimestamped_file(self):
        now = datetime(2026, 9, 17, 19, 0, tzinfo=KST)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir()
            stale = logs / "old_batch.log"
            stale.write_text("no dates in this file\n", encoding="utf-8")
            old_mtime = (now - timedelta(days=20)).timestamp()
            os_utime = __import__("os").utime
            os_utime(stale, (old_mtime, old_mtime))
            cleanup_logs(root, keep_days=7, now=now)
            self.assertFalse(stale.exists())

    def test_dry_run_does_not_rewrite(self):
        now = datetime(2026, 9, 17, 19, 0, tzinfo=KST)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir()
            old = (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
            p = logs / "app.log"
            original = f"{old} gone\n"
            p.write_text(original, encoding="utf-8")
            summary = cleanup_logs(root, keep_days=7, now=now, dry_run=True)
            self.assertEqual(p.read_text(encoding="utf-8"), original)
            self.assertTrue(any(r.action in ("trim", "delete") for r in summary.results))

    def test_format_bytes(self):
        self.assertEqual(format_bytes(500), "500B")
        self.assertIn("KB", format_bytes(2048))


if __name__ == "__main__":
    unittest.main()
