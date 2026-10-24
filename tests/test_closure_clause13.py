"""
2026-10-24：臨時休市(颱風)偵測與處置期間順延、第十三款預估。全部假資料，不連網。
執行：cd 專案根目錄 && uv run python -m unittest tests.test_closure_clause13 -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'automation'))

import notify_disposal_golden as n  # noqa: E402
from core import clause13 as c13  # noqa: E402
from core import market_closure as mc  # noqa: E402
from core import official_quotes as oq  # noqa: E402
from core.utils import DateUtils  # noqa: E402


def dgpa_page(update, taipei):
    return (f"<div>更新時間：{update} 12:11:03</div><TABLE>"
            f"<TR><TD headers='city_Name Keelung City'><FONT>基隆市</FONT></TD><TD><FONT color=#FF0000 >今天停止上班、停止上課。</FONT></TD></TR>"
            f"<TR><TD headers='city_Name Taipei City'><FONT>臺北市</FONT></TD><TD><FONT color=#FF0000 >{taipei}  </FONT></TD></TR>"
            f"<TR><TD headers='city_Name New Taipei City'><FONT>新北市</FONT></TD><TD><FONT>今天未達停止上班及上課標準。</FONT></TD></TR>"
            f"</TABLE>")


class DgpaParseTest(unittest.TestCase):
    def test_today_closed(self):
        r = mc.parse_dgpa(dgpa_page("2026/10/23", "今天已達停止上班及上課標準。"))
        self.assertEqual(r[date(2026, 10, 23)][0], "closed")

    def test_tomorrow_closed_today_open(self):
        r = mc.parse_dgpa(dgpa_page("2026/10/22", "今天照常上班、照常上課。明天停止上班、停止上課。"))
        self.assertEqual(r[date(2026, 10, 22)][0], "open")
        self.assertEqual(r[date(2026, 10, 23)][0], "closed")

    def test_afternoon_only_is_open(self):
        # 證交所：只宣布下午停止上班 → 照常開盤
        r = mc.parse_dgpa(dgpa_page("2026/10/23", "今天上午照常上班、上課，下午停止上班、停止上課。"))
        self.assertEqual(r[date(2026, 10, 23)][0], "open")

    def test_morning_closed(self):
        r = mc.parse_dgpa(dgpa_page("2026/10/23", "今天上午停止上班、停止上課，下午照常上班、上課。"))
        self.assertEqual(r[date(2026, 10, 23)][0], "closed")

    def test_class_only_and_not_reached(self):
        self.assertEqual(mc.classify("今天照常上班、停止上課。"), "open")
        self.assertEqual(mc.classify("今天未達停止上班及上課標準。"), "open")

    def test_no_taipei_row(self):
        r = mc.parse_dgpa("<div>更新時間：2026/10/20 08:00:00</div>無停班停課訊息")
        self.assertEqual(r[date(2026, 10, 20)][0], "open")


class ClosureCalendarTest(unittest.TestCase):
    def test_1023_is_unscheduled_closure(self):
        d = datetime(2026, 10, 23)
        self.assertFalse(DateUtils.is_trading_day(d))
        self.assertTrue(DateUtils.is_unscheduled_closure(d))
        self.assertFalse(DateUtils.is_unscheduled_closure(datetime(2026, 10, 26)))   # 光復節補假是既定假日

    def test_postpone_5day(self):
        # 8437：公告 10/23～10/30(5 個營業日，10/26 光復節補假已扣)，10/23 颱風 → 10/27～11/02
        d = n.postpone_for_closures(dict(code="8437", start=date(2026, 10, 23), end=date(2026, 10, 30)))
        self.assertEqual((d["start"], d["end"]), (date(2026, 10, 27), date(2026, 11, 2)))
        self.assertEqual(n.n_trading_days(d["start"], d["end"]), 5)
        self.assertEqual(d["end_official"], date(2026, 10, 30))

    def test_postpone_7day(self):
        # 4542：7 天累犯 10/23～11/03 → 10/27～11/04
        d = n.postpone_for_closures(dict(code="4542", start=date(2026, 10, 23), end=date(2026, 11, 3)))
        self.assertEqual((d["start"], d["end"]), (date(2026, 10, 27), date(2026, 11, 4)))
        self.assertEqual(n.n_trading_days(d["start"], d["end"]), 7)

    def test_postpone_ends_on_closure(self):
        # 3055：10/19～10/23 最後一天碰到颱風 → 10/19～10/27(10/26 既定假日)
        d = n.postpone_for_closures(dict(code="3055", start=date(2026, 10, 19), end=date(2026, 10, 23)))
        self.assertEqual(d["end"], date(2026, 10, 27))

    def test_postpone_middle(self):
        # 3287：10/22～10/29(10/22、23、27、28、29) → 10/22～10/30
        d = n.postpone_for_closures(dict(code="3287", start=date(2026, 10, 22), end=date(2026, 10, 29)))
        self.assertEqual((d["start"], d["end"]), (date(2026, 10, 22), date(2026, 10, 30)))

    def test_no_closure_unchanged(self):
        d = n.postpone_for_closures(dict(code="1", start=date(2026, 10, 27), end=date(2026, 11, 2)))
        self.assertNotIn("postponed", d)
        self.assertEqual(d["end"], date(2026, 11, 2))

    def test_missed_predictions_skip_closed_day(self):
        log = {"_cand": {"2026-10-23": [{"code": "6751", "name": "智聯服務"}]},
               "_pre_short": {"2026-10-23": [{"code": "2033", "name": "佳大"}]}}
        self.assertEqual(n.missed_predictions(log, date(2026, 10, 23), []), ([], []))

    def test_closure_note(self):
        self.assertIn("10/23 臨時休市", n.closure_note(date(2026, 10, 22), date(2026, 10, 27)))
        self.assertEqual(n.closure_note(date(2026, 10, 27), date(2026, 10, 28)), "")


class RecordHolidayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hol = os.path.join(self.tmp, "twse_holidays.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unscheduled_key(self):
        oq.record_holiday(date(2026, 10, 1), self.hol, unscheduled=True)
        with open(self.hol, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["holidays"], ["2026-10-01"])
        self.assertEqual(data["unscheduled"], ["2026-10-01"])

    def test_evening_empty_is_holiday(self):
        class C:
            def get_json(self, url, ref):
                return {"stat": "很抱歉，沒有符合條件的資料!"}
        d = date(2026, 10, 23)
        self.assertEqual(oq.fetch_day(C(), d, now=datetime(2026, 10, 23, 18, 30)).status, "holiday")
        self.assertEqual(oq.fetch_day(C(), d, now=datetime(2026, 10, 23, 15, 0)).status, "pending")
        self.assertEqual(oq.fetch_day(C(), d, today=d).status, "pending")      # 測試指定 today：維持舊行為


class FakeSrc:
    """6 個交易日的當沖/成交量。"""
    def __init__(self, rows, shares=None):
        self.rows = rows          # {date: (當沖股數, 成交股數, 收盤)}
        self._shares = shares

    def daytrade(self, market, d):
        return {"1234": self.rows[d][0]} if d in self.rows else None

    def price(self, code, d):
        return (self.rows[d][1], self.rows[d][2]) if d in self.rows else (None, None)

    def shares(self, code):
        return self._shares


class Clause13Test(unittest.TestCase):
    DAYS = [date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20),
            date(2026, 10, 21)]

    def test_hit(self):
        rows = {d: (7_000_000, 10_000_000, 100.0) for d in self.DAYS}        # 70%、7000 張、10 億
        o = c13.outlook("1234", "上市", date(2026, 10, 21), FakeSrc(rows, shares=100_000_000))
        self.assertTrue(o["hit"])
        self.assertIn("會出現第十三款", c13.describe(o, date(2026, 10, 22)))

    def test_fail_lots_and_turnover(self):
        rows = {d: (4_000_000, 6_000_000, 100.0) for d in self.DAYS}         # 4000 張 < 5000
        o = c13.outlook("1234", "上市", date(2026, 10, 21), FakeSrc(rows, shares=200_000_000))   # 週轉 3%
        self.assertFalse(o["hit"])
        self.assertIn("當沖量", o["fails"])
        self.assertIn("週轉率", o["fails"])
        self.assertAlmostEqual(o["need_vol_lots"], 10_000)

    def test_tpex_lower_lots(self):
        rows = {d: (2_500_000, 3_500_000, 100.0) for d in self.DAYS}         # 2500 張、3.5 億
        o = c13.outlook("1234", "上櫃", date(2026, 10, 21), FakeSrc(rows, shares=50_000_000))
        self.assertTrue(o["hit"])
        self.assertIn("推估", c13.describe(o, date(2026, 10, 22)))

    def test_missing_day(self):
        rows = {d: (7_000_000, 10_000_000, 100.0) for d in self.DAYS[1:]}
        o = c13.outlook("1234", "上市", date(2026, 10, 21), FakeSrc(rows))
        self.assertIsNone(o["hit"])
        self.assertIn("無法預估", c13.describe(o, date(2026, 10, 22)))

    def test_parse_daytrade_rejects_other_date(self):
        payload = {"stat": "OK", "date": "20261022", "tables": [{}, {"fields": ["證券代號", "證券名稱", "當日沖銷交易成交股數"],
                                                                   "data": [["2033", "佳大", "2,400,000"]]}]}
        self.assertEqual(c13.parse_daytrade(payload, date(2026, 10, 22)), {"2033": 2400000.0})
        self.assertIsNone(c13.parse_daytrade(payload, date(2026, 10, 23)))
        # 休市日 TWTB4U 回 OK 但只有「標的」清單、沒有成交股數欄
        holiday = {"stat": "OK", "date": "20261023", "tables": [{}, {"fields": ["證券代號", "證券名稱"], "data": [["2033", "佳大"]]}]}
        self.assertIsNone(c13.parse_daytrade(holiday, date(2026, 10, 23)))


if __name__ == "__main__":
    unittest.main()
