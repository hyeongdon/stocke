"""스캔 요약 — 전략 프로필별 분해."""
import unittest
from types import SimpleNamespace

from managers.auto_trade_scanner import (
    _count_targets_by_strategy,
    _format_pool_brief,
    _format_scan_summary,
    _target_strategy_key,
)


class ScanSummaryByStrategyTests(unittest.TestCase):
    def test_target_strategy_key(self):
        self.assertEqual(_target_strategy_key({"source": "sangtta"}), "sangtta")
        self.assertEqual(_target_strategy_key({"source": "breakout"}), "breakout")
        self.assertEqual(_target_strategy_key({"source": "ymgp"}), "ymgp")
        self.assertEqual(_target_strategy_key({"source": "jongga"}), "jongga")
        self.assertEqual(_target_strategy_key({"source": "screener"}), "legacy")
        self.assertEqual(_target_strategy_key({"source": "watchlist"}), "legacy")

    def test_count_and_pool_brief(self):
        targets = [
            {"source": "screener"},
            {"source": "screener"},
            {"source": "breakout"},
            {"source": "ymgp"},
            {"source": "ymgp"},
        ]
        by = _count_targets_by_strategy(targets)
        self.assertEqual(by["legacy"], 2)
        self.assertEqual(by["breakout"], 1)
        self.assertEqual(by["ymgp"], 2)
        self.assertEqual(by["sangtta"], 0)
        brief = _format_pool_brief(by)
        self.assertIn("거래대금 눌림목 2", brief)
        self.assertIn("수급 돌파 1", brief)
        self.assertIn("역매공파 2", brief)
        self.assertNotIn("상따", brief)

    def test_format_scan_summary_lines(self):
        settings = SimpleNamespace(
            min_change_rate_buy=None,
            signal_min_change_rate=None,
            signal_min_threshold=None,
        )
        lines = _format_scan_summary(
            {"gate": 3, "holding": 1},
            total=6,
            created=1,
            settings=settings,
            targets_by={"legacy": 3, "sangtta": 0, "breakout": 2, "ymgp": 1},
            stats_by={
                "legacy": {"gate": 2, "holding": 1},
                "breakout": {"gate": 1},
                "ymgp": {"signal_ok": 1},
            },
            created_by={"legacy": 0, "breakout": 0, "ymgp": 1},
        )
        self.assertGreaterEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("스캔 요약 — "))
        self.assertIn("전체 대상 6", lines[0])
        joined = "\n".join(lines)
        self.assertIn("[거래대금 눌림목]", joined)
        self.assertIn("[수급 돌파]", joined)
        self.assertIn("[역매공파]", joined)
        self.assertNotIn("[상따]", joined)


if __name__ == "__main__":
    unittest.main()
