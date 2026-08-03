"""종가배팅 pick_end 자동매수 미실행 → 체결 로그(FAILED) 사유 기록."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from managers.auto_trade_scanner import AutoTradeScanner
from managers.signal_manager import SignalStatus, SignalType, SignalManager


class RecordFailedSignalTests(unittest.TestCase):
    def test_save_new_failed_sets_reason(self):
        mgr = SignalManager()

        async def _run():
            with patch.object(mgr, "_get_existing_signal", AsyncMock(return_value=None)):
                with patch.object(mgr, "_save_signal_to_db", AsyncMock(return_value=42)):
                    fake_row = MagicMock()
                    fake_row.failure_reason = None
                    fake_db = MagicMock()
                    fake_db.query.return_value.filter.return_value.first.return_value = fake_row
                    with patch(
                        "managers.signal_manager.get_db",
                        return_value=iter([fake_db]),
                    ):
                        ok, msg = await mgr.record_failed_signal(
                            condition_id=99999,
                            stock_code="058610",
                            stock_name="에스피지",
                            signal_type=SignalType.STRATEGY,
                            failure_reason="종가배팅 자동매수 미실행: 슬롯 포화 (1/1)",
                            additional_data={"strategy": "jongga", "jongga_mode": "auto_miss"},
                        )
            self.assertTrue(ok)
            self.assertIn("저장", msg)
            self.assertTrue(
                str(fake_row.failure_reason).startswith("종가배팅 자동매수 미실행")
            )
            fake_db.commit.assert_called()

        asyncio.run(_run())

    def test_skip_when_already_filled(self):
        mgr = SignalManager()
        existing = MagicMock()
        existing.status = SignalStatus.FILLED.value

        async def _run():
            with patch.object(mgr, "_get_existing_signal", AsyncMock(return_value=existing)):
                ok, msg = await mgr.record_failed_signal(
                    condition_id=99999,
                    stock_code="058610",
                    stock_name="에스피지",
                    signal_type=SignalType.STRATEGY,
                    failure_reason="테스트",
                )
            self.assertFalse(ok)
            self.assertIn("FILLED", msg)

        asyncio.run(_run())


class JonggaAutoMissRecorderTests(unittest.TestCase):
    def test_records_once_and_marks_state(self):
        with patch("managers.auto_trade_scanner.KiwoomAPI"):
            scanner = AutoTradeScanner()
        st = {"biz_date": "2026-08-03", "status": "awaiting_pick", "candidates": []}
        saves = []

        async def _run():
            with patch(
                "managers.auto_trade_scanner.signal_manager.record_failed_signal",
                AsyncMock(return_value=(True, "실패 이력 저장")),
            ) as rec:
                with patch("managers.auto_trade_scanner.log_activity") as log_act:
                    await scanner._record_jongga_auto_miss(
                        st,
                        reason="종가배팅 슬롯 포화 (1/1)",
                        code="058610",
                        name="에스피지",
                        auto={"stock_code": "058610", "stock_name": "에스피지", "theme": "로봇"},
                        save_jongga_state=lambda s: saves.append(dict(s)),
                    )
                    # 두 번째 호출은 no-op
                    await scanner._record_jongga_auto_miss(
                        st,
                        reason="다른 사유",
                        code="058610",
                        name="에스피지",
                        save_jongga_state=lambda s: saves.append(dict(s)),
                    )
            self.assertEqual(rec.await_count, 1)
            self.assertTrue(st.get("auto_miss_logged"))
            self.assertEqual(st.get("status"), "auto_miss")
            self.assertIn("슬롯 포화", st.get("auto_miss_reason") or "")
            self.assertTrue(any(c.args[0] == "BUY" for c in log_act.call_args_list))
            self.assertEqual(len(saves), 1)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
