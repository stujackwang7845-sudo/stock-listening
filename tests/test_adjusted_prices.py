"""還原股價補值(core/adjusted_prices.py，2026-10-14)：±10% 檢查、停牌間隔、未收盤不補。用假資料，不讀 parquet。"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from core import adjusted_prices as ap  # noqa: E402

FAKE = {(2026, 9): {"1111": {"2026-09-30": 100.0}, "2222": {"2026-09-01": 50.0}},
        (2026, 10): {"1111": {"2026-10-01": 105.0, "2026-10-02": 120.0, "2026-10-05": 108.0},
                     "2222": {"2026-10-05": 51.0}}}


class AdjustedPricesTest(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(ap, "_month", side_effect=lambda y, m: FAKE.get((y, m), {}))
        self.p.start()
        self.a = mock.patch.object(ap, "available", return_value=True)
        self.a.start()

    def tearDown(self):
        self.p.stop()
        self.a.stop()

    def test_daily_change_and_month_boundary(self):
        self.assertEqual(ap.daily_change("1111", "2026-10-01"), (5.0, ""))       # 前一天在上個月
        self.assertEqual(ap.daily_change("1111", "2026-10-05")[0], -10.0)        # 剛好跌停可以

    def test_over_limit_rejected(self):
        self.assertEqual(ap.daily_change("1111", "2026-10-02"), (None, "超過 ±10%"))   # +14.29%

    def test_long_gap_rejected(self):
        v, why = ap.daily_change("2222", "2026-10-05")                            # 隔 34 天(停牌)
        self.assertIsNone(v)
        self.assertIn("隔", why)

    def test_fill_changes(self):
        ch = {"-1": None, "處置日": None, "+1": 3.0, "處置期間漲跌幅": None, "出關日": None}
        dt = {"-1": "2026-10-01", "處置日": "2026-10-02", "+1": "2026-10-05", "出關日": "2999-01-01"}
        got = ap.fill_changes("1111", ch, dt)
        self.assertEqual(got, ["-1"])                     # 處置日超過 ±10% 不補；未來日不補；期間欄不碰
        self.assertEqual(ch["-1"], 5.0)
        self.assertIsNone(ch["處置日"])
        self.assertEqual(ch["+1"], 3.0)                   # 已有值不動


    def test_period_change(self):
        self.assertAlmostEqual(ap.period_change("1111", "2026-09-30", "2026-10-01"), 5.0)
        self.assertIsNone(ap.period_change("1111", "2026-09-30", "2026-10-05"))   # 途中 10/02 +14% 可疑


if __name__ == "__main__":
    unittest.main()
