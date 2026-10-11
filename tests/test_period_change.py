"""
處置統計「處置期間漲跌幅」欄(core/disposal_stats_engine.add_period_change)測試，用假的股價庫。
執行：cd 專案根目錄 && uv run python -m unittest tests.test_period_change -v
"""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

from core.disposal_stats_engine import PERIOD_COL, add_period_change, sort_change_columns  # noqa: E402


class FakePriceDB:
    def __init__(self, closes):
        self.closes = closes

    def get_price(self, code, date):
        c = self.closes.get(date)
        return {"close": c} if c is not None else None


HEADERS = ["-1", "處置日", "+1", "處置結束", "出關日"]
DATES = {"-1": "2026-10-05", "處置日": "2026-10-06", "+1": "2026-10-07", "處置結束": "2026-10-13", "出關日": "2026-10-14"}


class PeriodChangeTest(unittest.TestCase):
    def test_value_and_position(self):
        h, c = add_period_change(HEADERS, {"處置結束": 1.0}, DATES, "6672",
                                 FakePriceDB({"2026-10-05": 100.0, "2026-10-13": 112.5}))
        self.assertEqual(h, ["-1", "處置日", "+1", "處置結束", PERIOD_COL, "出關日"])
        self.assertEqual(c[PERIOD_COL], 12.5)    # 末日收盤 / 處置前一天收盤 - 1

    def test_missing_close_or_ongoing(self):
        h, c = add_period_change(HEADERS, {}, DATES, "6672", FakePriceDB({"2026-10-05": 100.0}))
        self.assertNotIn(PERIOD_COL, h)
        h, c = add_period_change(["-1", "處置日"], {}, {"-1": "2026-10-05"}, "6672", FakePriceDB({}))
        self.assertNotIn(PERIOD_COL, h)          # 還沒有「處置結束」(進行中)

    def test_column_order(self):
        cols = sort_change_columns(["出關日", PERIOD_COL, "處置結束", "+1", "-1", "處置日"])
        self.assertEqual(cols, ["-1", "處置日", "+1", "處置結束", PERIOD_COL, "出關日"])


if __name__ == "__main__":
    unittest.main()
