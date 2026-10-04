"""
進處置條件分區測試(core/conditions_engine.py + calculator 第[1]款分區)。
規則：連3日第一款 / 連5日一至八款 / 10日內6次 / 30日內12次，任一達成進處置。
執行：cd 專案根目錄 && uv run python -m unittest tests.test_conditions_engine -v
"""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))

import pandas as pd

from core.conditions_engine import (build_pred_dates, build_history_items,
                                    needed_for_conditions, compute_stock_conditions)
from core.predictor import DispositionPredictor

ANCHOR = datetime(2026, 10, 2)


def _clauses(seq):
    """seq 由舊到新，最後一筆是 ANCHOR 當天，例如 ["一", "一", "二"]；None 表示當天沒被注意。"""
    dates = build_pred_dates(ANCHOR, 30)
    return {d: c for d, c in zip(dates[-len(seq):], seq) if c}


def _needed(seq, official_reason=""):
    hist = build_history_items(_clauses(seq), [], build_pred_dates(ANCHOR, 30), ANCHOR)
    tp = DispositionPredictor.get_trigger_progress(hist)
    return needed_for_conditions(tp, official_reason)


def _price_df(daily_pct, n=100):
    """每天固定漲 daily_pct%，最後一天是 ANCHOR。"""
    idx = pd.bdate_range(end=ANCHOR, periods=n)
    closes = [10.0]
    for _ in range(n - 1):
        closes.append(round(closes[-1] * (1 + daily_pct / 100), 2))
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                       "Volume": [1_000_000] * n}, index=idx)
    df.index.name = "Date"
    return df


class _OfflineFetcher:
    """測試不連網：不查處置歷史 API。"""
    def fetch_stock_disposition_history(self, *a, **k):
        return []


def _sections(seq, daily_pct=5, official_reason=""):
    """回傳 {'進處置': [款...], '聽牌': [款...]}"""
    res = compute_stock_conditions("9999", "上市", "測試", ANCHOR, _clauses(seq), [],
                                   official_reason=official_reason, fetcher=_OfflineFetcher(),
                                   history_df=_price_df(daily_pct), shares_outstanding=10_000_000)
    out = {"進處置": [], "聽牌": []}
    cur = None
    for line in res["lines"]:
        if "→ 進處置:" in line:
            cur = "進處置"
        elif "→ 變聽牌" in line:
            cur = "聽牌"
        elif cur and line.startswith("<b>["):
            tag = line.split("]")[0].replace("<b>", "") + "]"
            out[cur].append("[1]" if tag == "[1-1]" else tag)  # [1-1] 是第一款的差價子條件
    return out, res


class TestNeededCounts(unittest.TestCase):
    def test_c1_twice(self):
        # 連兩次第一款：明天再第一款就進處置；任一款才連2次
        self.assertEqual(_needed(["一", "一"]), (1, 3))

    def test_c1_twice_then_other_clause(self):
        # 第三次是二至八款：第一款連續中斷；任一款連3次，還要再連2次才進處置
        self.assertEqual(_needed(["一", "一", "二"]), (3, 2))

    def test_streak_four(self):
        self.assertEqual(_needed(["二", "三", "二", "四"])[1], 1)

    def test_ten_day_five_times(self):
        # 10日內5次但不連續 → 明天再一次就 10日6次
        seq = ["二", None, "三", None, "二", None, "四", None, "二"]
        self.assertEqual(_needed(seq), (3, 1))

    def test_official_overrides(self):
        # 本地資料缺漏時，官方聽牌原因為準
        self.assertEqual(_needed([], "115年10月01日至115年10月02日連續二次")[0], 1)
        self.assertEqual(_needed([], "等九個營業日已有五次")[1], 1)
        self.assertEqual(_needed([], "連續四次")[1], 1)
        self.assertEqual(_needed([], "等二十九個營業日已有十一次")[1], 1)

    def test_reset_after_trigger(self):
        # 連3日第一款觸發後隔天歸零
        self.assertEqual(_needed(["一", "一", "一", "一"]), (2, 4))


class TestSections(unittest.TestCase):
    def test_c1_twice(self):
        sec, _ = _sections(["一", "一"])
        self.assertEqual(sec["進處置"], ["[1]"])
        self.assertNotIn("[2]", sec["進處置"] + sec["聽牌"])

    def test_c1_twice_then_other_clause(self):
        sec, _ = _sections(["一", "一", "二"])
        self.assertEqual(sec["進處置"], [])
        self.assertIn("[1]", sec["聽牌"])
        self.assertIn("[2]", sec["聽牌"])

    def test_ten_day_five_times_c1_also_enters(self):
        # 第一款也是一至八款之一：10日已5次時，明天中第一款一樣進處置
        sec, _ = _sections(["二", None, "三", None, "二", None, "四", None, "二"])
        self.assertIn("[1]", sec["進處置"])
        self.assertIn("[2]", sec["進處置"])

    def test_must_enter_only_when_disposal(self):
        # 每天漲9%：明天跌停也達第一款
        _, res = _sections(["一", "一"], daily_pct=9)
        self.assertTrue(any("必進處置" in l for l in res["lines"]))
        _, res = _sections(["一", "一", "二"], daily_pct=9)
        text = "".join(res["lines"])
        self.assertNotIn("必進處置", text)
        self.assertIn("必聽牌", text)


if __name__ == "__main__":
    unittest.main()
