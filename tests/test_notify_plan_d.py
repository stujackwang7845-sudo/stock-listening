"""
Telegram 推播方案 D(scripts/automation/notify_disposal_golden.py)的純計算部分測試：
漲停價、停損價、進場日(5 天初犯前一天、其餘首日)、停損觸發與還原報酬、盤前候選條件文字擷取。
全部用假資料，不連網。  執行：cd 專案根目錄 && uv run python -m unittest tests.test_notify_plan_d -v
"""
import os
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'automation'))

import notify_disposal_golden as n  # noqa: E402
import preclose_candidates as pc  # noqa: E402


class PriceRulesTest(unittest.TestCase):
    def test_limit_up_price(self):
        self.assertEqual(n.limit_up_price(100.0), 110.0)
        self.assertEqual(n.limit_up_price(9.55), 10.5)       # 10.505 在 10~50 元區間，升降單位 0.05
        self.assertEqual(n.limit_up_price(806.0), 886.0)     # 886.6 → 500~1000 元區間單位 1
        self.assertEqual(n.limit_up_price(55.7), 61.2)       # 61.27 → 單位 0.1 無條件捨去

    def test_stop_price(self):
        self.assertEqual(n.stop_price_of(100.0), 92.0)
        self.assertEqual(n.stop_price_of(806.0), 741.0)      # 741.52 向下取到單位 1
        self.assertEqual(n.stop_price_of(203.0), 186.5)      # 186.76 → 單位 0.5


def disposal(code, start, end, n_days):
    d = dict(code=code, name="測試", market="上市", start=start, end=end, n_days=n_days)
    d["entry"] = n.prev_trading_day(start) if n_days == 5 else start
    return d


class EntryDayTest(unittest.TestCase):
    def test_five_day_enters_day_before(self):
        # 2026-10-12(一) 起處置：前一個交易日是 10/08(10/09 休市、週末)
        self.assertEqual(n.prev_trading_day(date(2026, 10, 12)), date(2026, 10, 8))
        self.assertEqual(n.n_trading_days(date(2026, 10, 12), date(2026, 10, 16)), 5)


class CalcReturnTest(unittest.TestCase):
    D = disposal("1111", date(2026, 10, 12), date(2026, 10, 16), 5)   # 進場日 10/08

    def build(self, closes):
        daily = {("1111", d): (1000.0, c) for d, c in closes.items()}
        return daily

    def test_no_stop(self):
        daily = self.build({date(2026, 10, 8): 100.0, date(2026, 10, 12): 95.0, date(2026, 10, 13): 108.0})
        rr = n.calc_return(self.D, date(2026, 10, 13), {}, daily, {})
        self.assertEqual((rr["entry"], rr["entry_date"], rr["stop_price"]), (100.0, date(2026, 10, 8), 92.0))
        self.assertIsNone(rr["stopped"])
        self.assertAlmostEqual(rr["adj"], 8.0)

    def test_stop_triggered_first_day_and_exit_at_that_close(self):
        daily = self.build({date(2026, 10, 8): 100.0, date(2026, 10, 12): 91.5, date(2026, 10, 13): 120.0})
        rr = n.calc_return(self.D, date(2026, 10, 13), {}, daily, {})
        self.assertEqual(rr["stopped"]["date"], date(2026, 10, 12))
        self.assertAlmostEqual(rr["adj"], -8.5)              # 當天收盤出場，後面的反彈不算

    def test_exactly_at_stop_price_counts(self):
        daily = self.build({date(2026, 10, 8): 100.0, date(2026, 10, 12): 92.0})
        rr = n.calc_return(self.D, date(2026, 10, 12), {}, daily, {})
        self.assertIsNotNone(rr["stopped"])                  # 收盤 ≤ 進場價×0.92

    def test_ex_dividend_does_not_trigger_false_stop(self):
        # 10/12 除息：收盤 90 但除息前收盤 100、參考價 91 → 還原後 90×(100/91)/100 = -1.1%，不該停損
        daily = self.build({date(2026, 10, 8): 100.0, date(2026, 10, 12): 90.0})
        corp = {"1111": [(date(2026, 10, 12), 100.0, 91.0)]}
        rr = n.calc_return(self.D, date(2026, 10, 12), {}, daily, corp)
        self.assertIsNone(rr["stopped"])
        self.assertLess(abs(rr["adj"] + 1.1), 0.1)

    def test_limit_up_entry_detected(self):
        quotes = {}
        daily = {("1111", date(2026, 10, 7)): (1.0, 100.0), ("1111", date(2026, 10, 8)): (1.0, 110.0)}
        self.assertTrue(n.is_limit_up("1111", date(2026, 10, 8), quotes, daily))
        daily[("1111", date(2026, 10, 8))] = (1.0, 109.5)
        self.assertFalse(n.is_limit_up("1111", date(2026, 10, 8), quotes, daily))


class TriggerLinesTest(unittest.TestCase):
    def test_trigger_lines_only_disposal_section(self):
        calc = ["最新收盤: 51.2  漲停價: 56.30  跌停價: 46.05",
                "<br><b>明日達以下任一 → 進處置:</b>",
                "<b>[1] 6日漲幅 > 32% (上市)</b><br>目標 50.60 (可跌1.17%)<br>",
                "<br><b>明日達以下任一 → 變聽牌（還不會進處置）:</b>",
                "<b>[2] 長期漲幅條款 (上市)</b><br>51.3 進處置<br>"]
        self.assertEqual(pc.trigger_lines(calc), ["[1] 6日漲幅 > 32% (上市) 目標 50.60 (可跌1.17%)"])
        self.assertEqual(pc.trigger_lines([]), [])


if __name__ == "__main__":
    unittest.main()
