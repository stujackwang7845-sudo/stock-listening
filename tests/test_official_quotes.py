"""
P3 官方行情模組測試(core/official_quotes.py)，全部離線(假 client)，資料庫寫在暫存目錄。
執行：cd 專案根目錄 && uv run python -m unittest tests.test_official_quotes -v
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

from core import official_quotes as oq  # noqa: E402

TWSE_FIELDS = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價",
               "收盤價", "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "本益比"]
TPEX_FIELDS = ["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)", "成交筆數"]


def twse_payload(d, rows):
    roc = f"{d.year - 1911}年{d.month:02d}月{d.day:02d}日"
    return {"stat": "OK", "date": d.strftime("%Y%m%d"),
            "tables": [{"title": f"{roc} 價格指數", "fields": ["指數"], "data": []},
                       {"title": f"{roc} 每日收盤行情(全部)", "fields": TWSE_FIELDS, "data": rows}]}


def tpex_payload(d, rows, mgmt=()):
    roc = f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"
    return {"stat": "ok", "date": d.strftime("%Y%m%d"),
            "tables": [{"title": "上櫃股票行情", "date": roc, "fields": TPEX_FIELDS, "data": rows},
                       {"title": "管理股票", "fields": TPEX_FIELDS, "data": list(mgmt)}]}


def twse_row(code, close, vol="1,234,000"):
    return [code, "名稱", vol, "10", "999", "10.00", "11.00", "9.50", close, "+", "0.5",
            "", "", "", "", "0.00"]


def tpex_row(code, close, vol="2,000"):
    return [code, "櫃名", close, "0.10 ", "20.00", "21.00", "19.50", "20.3", vol, "40000", "5"]


class ParseTest(unittest.TestCase):
    D = date(2026, 10, 2)

    def test_twse_basic_and_filters(self):
        rows = oq.parse_twse_quotes(twse_payload(self.D, [
            twse_row("2330", "1,005.00"), twse_row("0050", "180.5"), twse_row('="00878"', "22"),
            twse_row("備註:", "--"), twse_row("1101", "--", "0")]), self.D)
        self.assertEqual(rows["2330"]["close"], 1005.0)
        self.assertEqual(rows["2330"]["volume"], 1234000.0)   # 股數，不換算成張
        self.assertEqual(rows["2330"]["market"], "上市")
        self.assertIn("00878", rows)
        self.assertNotIn("備註:", rows)
        self.assertIsNone(rows["1101"]["close"])               # 無成交

    def test_twse_date_mismatch_rejected(self):
        p = twse_payload(self.D, [twse_row("2330", "1000")])
        p["date"] = "20261001"
        with self.assertRaises(oq.DateMismatchError):
            oq.parse_twse_quotes(p, self.D)

    def test_twse_title_mismatch_rejected(self):
        p = twse_payload(self.D, [twse_row("2330", "1000")])
        p["tables"][1]["title"] = "115年10月01日 每日收盤行情(全部)"
        with self.assertRaises(oq.DateMismatchError):
            oq.parse_twse_quotes(p, self.D)

    def test_twse_no_data(self):
        self.assertIsNone(oq.parse_twse_quotes({"stat": "很抱歉，沒有符合條件的資料!"}, self.D))

    def test_tpex_tables_and_code_filter(self):
        rows = oq.parse_tpex_quotes(tpex_payload(
            self.D, [tpex_row("6488", "500"), tpex_row("006201", "20"), tpex_row("31675", "105"),
                     tpex_row("700001", "1.2")],
            mgmt=[tpex_row("1240", "10")]), self.D)
        self.assertEqual(set(rows), {"6488", "006201", "1240"})   # 可轉債/權證排除，管理股票要有
        self.assertEqual(rows["6488"]["market"], "上櫃")

    def test_tpex_empty_and_mismatch(self):
        self.assertIsNone(oq.parse_tpex_quotes(tpex_payload(self.D, []), self.D))
        p = tpex_payload(self.D, [tpex_row("6488", "500")])
        p["tables"][0]["date"] = "115/10/01"
        with self.assertRaises(oq.DateMismatchError):
            oq.parse_tpex_quotes(p, self.D)

    def test_ratios(self):
        twse = {"stat": "OK", "date": "20261002",
                "fields": ["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"],
                "data": [["1101", "台泥", "25.35", "3.16", 114, "-", "0.82", "115/2"],
                         ["2330", "台積電", "1005", "1.2", 114, "25.10", "7.5", "115/2"]]}
        r = oq.parse_twse_ratios(twse, self.D)
        self.assertEqual(r["1101"], (None, 0.82))
        self.assertEqual(r["2330"], (25.1, 7.5))
        tpex = {"stat": "ok", "date": "20261002", "tables": [{"date": "115/10/02",
                "fields": ["股票代號", "名稱", "本益比", "每股股利", "股利年度", "殖利率(%)", "股價淨值比", "財報年/季"],
                "data": [["1240", "茂生農經", "10.04", "0.5", 114, "0.93", "1.59", "115Q2"]]}]}
        self.assertEqual(oq.parse_tpex_ratios(tpex, self.D)["1240"], (10.04, 1.59))


class FakeClient:
    """依 URL 回傳假資料；days 是 {date: (twse_rows or None, tpex_rows or None)}。"""

    def __init__(self, days):
        self.days = days
        self.calls = []

    def get_json(self, url, referer):
        self.calls.append(url)
        for d, (tw, tp) in self.days.items():
            if d.strftime("%Y%m%d") in url and "MI_INDEX" in url:
                return twse_payload(d, tw) if tw else {"stat": "沒有資料"}
            if d.strftime("%Y/%m/%d") in url and "dailyQuotes" in url:
                return tpex_payload(d, tp or [])
            if d.strftime("%Y%m%d") in url and "BWIBBU" in url:
                return {"stat": "OK", "date": d.strftime("%Y%m%d"),
                        "fields": ["證券代號", "本益比", "股價淨值比"], "data": [["2330", "25", "7"]]}
            if d.strftime("%Y/%m/%d") in url and "peQryDate" in url:
                return {"stat": "ok", "date": d.strftime("%Y%m%d"), "tables": []}
        raise AssertionError(f"沒預期的請求 {url}")


def full_day(code_close):
    return ([twse_row(c, p) for c, p in code_close], [tpex_row("6488", "500")])


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "market_data.db")
        self.hol = os.path.join(self.tmp, "twse_holidays.json")

    def run_backfill(self, days, end, n, today):
        client = FakeClient(days)
        res = oq.backfill(end, n, self.db, self.hol, client=client, today=today, log=lambda *_: None)
        return res, client

    def test_holiday_partial_pending_and_resume(self):
        # 10/02(五) 正常、10/01(四) 兩邊都空=休市、9/30(三) 只有上櫃=partial、9/29(二) 正常
        days = {date(2026, 10, 2): full_day([("2330", "1000")]),
                date(2026, 10, 1): (None, None),
                date(2026, 9, 30): (None, [tpex_row("6488", "1")]),
                date(2026, 9, 29): full_day([("2330", "990")])}
        res, client = self.run_backfill(days, date(2026, 10, 2), 3, today=date(2026, 10, 5))
        self.assertEqual(res[date(2026, 10, 2)], "ok")
        self.assertEqual(res[date(2026, 10, 1)], "holiday")
        self.assertEqual(res[date(2026, 9, 30)], "partial")
        self.assertEqual(res[date(2026, 9, 29)], "ok")
        with open(self.hol, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["holidays"], ["2026-10-01"])   # partial 不記休市
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_history WHERE date='2026-09-30'").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT per, pbr FROM ratios WHERE stock_id='2330'").fetchall(), [(25.0, 7.0)])
        conn.close()

        # 續跑：9/29 已存在(列數門檻設 1 方便測)，不應再請求
        orig = oq.stored_days
        oq.stored_days = lambda db, min_rows=1000: orig(db, min_rows=1)
        try:
            res2, client2 = self.run_backfill(days, date(2026, 10, 2), 3, today=date(2026, 10, 5))
        finally:
            oq.stored_days = orig
        self.assertEqual(res2[date(2026, 9, 29)], "stored")
        self.assertFalse(any("20260929" in u or "2026/09/29" in u for u in client2.calls))

    def test_today_empty_is_pending_not_holiday(self):
        days = {date(2026, 10, 5): (None, None), date(2026, 10, 2): full_day([("2330", "1000")])}
        res, _ = self.run_backfill(days, date(2026, 10, 5), 1, today=date(2026, 10, 5))
        self.assertEqual(res[date(2026, 10, 5)], "pending")
        self.assertFalse(os.path.exists(self.hol))


if __name__ == "__main__":
    unittest.main()
