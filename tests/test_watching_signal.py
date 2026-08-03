"""WATCHING(관측) → PENDING 승격 · 슬롯 미점유."""
import unittest
from unittest.mock import MagicMock

from managers.signal_manager import (
    BUY_SLOT_STATUSES,
    SignalStatus,
    SignalType,
    signal_manager,
)
from utils.auto_trade_engine import (
    classify_breakout_wait_kind,
    is_breakout_watching_reason,
)


class WatchingHelpersTests(unittest.TestCase):
    def test_classify_wait_kinds(self):
        self.assertEqual(
            classify_breakout_wait_kind("MA20 유예 대기 (2/3봉)"),
            "ma20_grace",
        )
        self.assertEqual(
            classify_breakout_wait_kind("진입 확인 대기 (HARD·SOFT·HOLD)"),
            "entry_confirm",
        )
        self.assertEqual(
            classify_breakout_wait_kind("돌파 전 (… HOLD 대기)"),
            "hold",
        )
        self.assertIsNone(classify_breakout_wait_kind("MA20 상회 아님 (종가 6,250)"))
        self.assertIsNone(classify_breakout_wait_kind("거래량 부족 (1.0배)"))
        self.assertTrue(is_breakout_watching_reason("MA20 유예 대기 (1/3봉)"))
        self.assertFalse(is_breakout_watching_reason("과열 컷"))

    def test_watching_not_in_slot_statuses(self):
        self.assertNotIn(SignalStatus.WATCHING.value, BUY_SLOT_STATUSES)
        self.assertIn(SignalStatus.PENDING.value, BUY_SLOT_STATUSES)


class WatchingSignalManagerTests(unittest.TestCase):
    def test_promote_watching_to_pending_updates_meta(self):
        import asyncio

        signal = MagicMock()
        signal.id = 99
        signal.status = "WATCHING"
        signal.stock_name = "테스트"
        signal.stock_code = "123456"
        signal.additional_data = {
            "strategy": "breakout",
            "wait_kind": "ma20_grace",
            "wait_reason": "MA20 유예 대기 (1/3봉)",
            "order_ready": False,
            "level_price": 1000,
        }
        signal.failure_reason = None

        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = signal

        class _Db:
            def __iter__(self):
                yield session

        from managers import signal_manager as sm_mod

        orig = sm_mod.get_db
        sm_mod.get_db = lambda: _Db()
        try:
            ok, msg = asyncio.run(
                signal_manager.promote_watching_to_pending(
                    99, additional_data={"confirm_close": 1100}
                )
            )
            self.assertTrue(ok, msg)
            self.assertEqual(signal.status, "PENDING")
            self.assertTrue(signal.additional_data.get("order_ready"))
            self.assertNotIn("wait_kind", signal.additional_data)
            self.assertEqual(signal.additional_data.get("confirm_close"), 1100)
            session.commit.assert_called()
        finally:
            sm_mod.get_db = orig


if __name__ == "__main__":
    unittest.main()
