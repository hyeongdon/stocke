import os
import tempfile
import time
import unittest
from unittest import mock

from utils import market_funds as mf

DEPOSIT_RAW = [
    {"TMPV1": "20261012", "TMPV2": 100438881, "TMPV3": 39747976, "TMPV4": 108454610,
     "TMPV5": 1063467, "TMPV6": 27083, "TMPV7": 2.7},
    {"TMPV1": "20261008", "TMPV2": 100109941, "TMPV3": 39391418, "TMPV4": 109146122,
     "TMPV5": 991462, "TMPV6": 6359, "TMPV7": 0.7},
]
CREDIT_RAW = [
    {"TMPV1": "20261012", "TMPV2": 34288699, "TMPV3": 26715212, "TMPV4": 7573487,
     "TMPV5": 40465, "TMPV6": 33947, "TMPV7": 6518, "TMPV8": 0, "TMPV9": 26658051},
    {"TMPV1": "20261008", "TMPV2": 33811321, "TMPV3": 26324143, "TMPV4": 7487178,
     "TMPV5": 42343, "TMPV6": 35356, "TMPV7": 6987, "TMPV8": 0, "TMPV9": 26395073},
]


def _fake_fetch(deposit_exc=None, credit_exc=None):
    def fetch(obj_nm, start, end):
        if obj_nm == mf.OBJ_DEPOSIT:
            if deposit_exc:
                raise deposit_exc
            return DEPOSIT_RAW
        if credit_exc:
            raise credit_exc
        return CREDIT_RAW
    return fetch


class MarketFundsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = mock.patch.object(
            mf, "SNAPSHOT_PATH", os.path.join(self.tmp.name, "_market_funds_snapshot.json")
        )
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.tmp.cleanup()

    def test_parse_deposit_sorted_ascending(self):
        rows = mf.parse_deposit_rows(DEPOSIT_RAW)
        self.assertEqual([r["date"] for r in rows], ["2026-10-08", "2026-10-12"])
        self.assertEqual(rows[-1]["deposit"], 100438881)
        self.assertEqual(rows[-1]["forced_sale_pct"], 2.7)

    def test_parse_credit_fields(self):
        last = mf.parse_credit_rows(CREDIT_RAW)[-1]
        self.assertEqual(last["credit"], 34288699)
        self.assertEqual(last["credit_kospi"] + last["credit_kosdaq"], last["credit"])
        self.assertEqual(last["stock_backed_loan"], 26658051)

    def test_merge_series_by_date(self):
        merged = mf.merge_series(
            mf.parse_deposit_rows(DEPOSIT_RAW[:1]), mf.parse_credit_rows(CREDIT_RAW)
        )
        self.assertEqual(len(merged), 2)
        self.assertNotIn("deposit", merged[0])
        self.assertEqual(merged[1]["deposit"], 100438881)
        self.assertEqual(merged[1]["credit"], 34288699)

    def test_fresh_snapshot_skips_api(self):
        with mock.patch.object(mf, "_fetch_kofia", side_effect=_fake_fetch()) as fetch:
            first = mf.fetch_market_funds()
            second = mf.fetch_market_funds()
        self.assertEqual(fetch.call_count, 2)
        self.assertFalse(second["stale"])
        self.assertEqual(first["rows"], second["rows"])

    def test_force_refetches(self):
        with mock.patch.object(mf, "_fetch_kofia", side_effect=_fake_fetch()) as fetch:
            mf.fetch_market_funds()
            mf.fetch_market_funds(force=True)
        self.assertEqual(fetch.call_count, 4)

    def test_expired_snapshot_refetches(self):
        with mock.patch.object(mf, "_fetch_kofia", side_effect=_fake_fetch()) as fetch:
            mf.fetch_market_funds()
            with mock.patch.object(mf.time, "time", return_value=time.time() + mf.SNAPSHOT_TTL_SEC + 1):
                mf.fetch_market_funds()
        self.assertEqual(fetch.call_count, 4)

    def test_total_failure_returns_stale_snapshot(self):
        with mock.patch.object(mf, "_fetch_kofia", side_effect=_fake_fetch()):
            ok = mf.fetch_market_funds()
        err = RuntimeError("down")
        with mock.patch.object(mf, "_fetch_kofia", side_effect=_fake_fetch(err, err)):
            out = mf.fetch_market_funds(force=True)
        self.assertTrue(out["stale"])
        self.assertEqual(len(out["errors"]), 2)
        self.assertEqual(out["rows"], ok["rows"])

    def test_total_failure_without_snapshot_raises(self):
        err = RuntimeError("down")
        with mock.patch.object(mf, "_fetch_kofia", side_effect=_fake_fetch(err, err)):
            with self.assertRaises(RuntimeError):
                mf.fetch_market_funds()

    def test_partial_failure_keeps_previous_series_and_retries_soon(self):
        with mock.patch.object(mf, "_fetch_kofia", side_effect=_fake_fetch()):
            mf.fetch_market_funds()
        with mock.patch.object(
            mf, "_fetch_kofia", side_effect=_fake_fetch(credit_exc=RuntimeError("down"))
        ):
            out = mf.fetch_market_funds(force=True)
        self.assertTrue(out["stale"])
        self.assertEqual(out["rows"][-1]["credit"], 34288699)
        snap = mf.load_snapshot()
        age = time.time() - snap["at"]
        self.assertGreater(age, mf.SNAPSHOT_TTL_SEC - mf.ERROR_RETRY_SEC - 5)


if __name__ == "__main__":
    unittest.main()
