"""스캔 요약 — 전략 프로필별 분해."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from managers.auto_trade_scanner import (
    _count_targets_by_strategy,
    _format_pool_brief,
    _format_scan_summary,
    _log_ma1592_scan_heartbeat,
    _target_strategy_key,
)


class ScanSummaryByStrategyTests(unittest.TestCase):
    def test_target_strategy_key(self):
        self.assertEqual(_target_strategy_key({"source": "sangtta"}), "sangtta")
        self.assertEqual(_target_strategy_key({"source": "breakout"}), "breakout")
        self.assertEqual(_target_strategy_key({"source": "ymgp"}), "legacy")
        self.assertEqual(_target_strategy_key({"source": "jongga"}), "jongga")
        self.assertEqual(_target_strategy_key({"source": "fractal"}), "fractal")
        self.assertEqual(_target_strategy_key({"source": "screener"}), "legacy")
        self.assertEqual(_target_strategy_key({"source": "watchlist"}), "legacy")

    def test_count_and_pool_brief(self):
        targets = [
            {"source": "screener"},
            {"source": "screener"},
            {"source": "breakout"},
            {"source": "fractal"},
            {"source": "fractal"},
        ]
        by = _count_targets_by_strategy(targets)
        self.assertEqual(by["legacy"], 2)
        self.assertEqual(by["breakout"], 1)
        self.assertEqual(by["fractal"], 2)
        self.assertEqual(by["sangtta"], 0)
        self.assertNotIn("ymgp", by)
        brief = _format_pool_brief(by)
        self.assertIn("거래대금 눌림목 2", brief)
        self.assertIn("수급 돌파 1", brief)
        self.assertIn("프랙탈 스캘핑 2", brief)
        self.assertNotIn("상따", brief)
        self.assertNotIn("역매공파", brief)

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
            targets_by={"legacy": 3, "sangtta": 0, "breakout": 2, "fractal": 1},
            stats_by={
                "legacy": {"gate": 2, "holding": 1},
                "breakout": {"gate": 1},
                "fractal": {"signal_ok": 1},
            },
            created_by={"legacy": 0, "breakout": 0, "fractal": 1},
        )
        self.assertGreaterEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("스캔 요약 — "))
        self.assertIn("전체 대상 6", lines[0])
        joined = "\n".join(lines)
        self.assertIn("[거래대금 눌림목]", joined)
        self.assertIn("[수급 돌파]", joined)
        self.assertIn("[프랙탈 스캘핑]", joined)
        self.assertNotIn("[상따]", joined)
        self.assertNotIn("[역매공파]", joined)

    @patch("managers.auto_trade_scanner.log_activity")
    def test_ma1592_scan_heartbeat(self, log_activity):
        settings = SimpleNamespace(use_ma1592=True)
        _log_ma1592_scan_heartbeat(
            settings,
            {"ma1592": 24},
            {"ma1592": {"watching": 18, "gate": 4, "holding": 2}},
            {"ma1592": 0},
        )
        log_activity.assert_called_once()
        args, kwargs = log_activity.call_args
        self.assertEqual(args[0], "SCANNER")
        self.assertIn("15/92 장부 24종 검사 완료", args[1])
        self.assertIn("대기 22", args[1])
        self.assertEqual(kwargs.get("strategy"), "ma1592")

    @patch("managers.auto_trade_scanner.log_activity")
    def test_ma1592_scan_heartbeat_skips_when_off(self, log_activity):
        settings = SimpleNamespace(use_ma1592=False)
        _log_ma1592_scan_heartbeat(settings, {"ma1592": 5}, {}, {})
        log_activity.assert_not_called()


if __name__ == "__main__":
    unittest.main()
