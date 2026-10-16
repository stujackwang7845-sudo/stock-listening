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


class ClauseFilterTest(unittest.TestCase):
    """第一款且 20MA 乖離 > 35% 不買(2026-10-12)。"""

    def make_db(self, rows):
        import sqlite3
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE attention_clauses (announce_date TEXT, code TEXT, clause_number INT)")
        c.executemany("INSERT INTO attention_clauses VALUES (?,?,?)", rows)
        c.commit()
        c.close()
        return path

    def test_clause_type(self):
        db = self.make_db([("2026-10-06", "1111", 1), ("2026-10-07", "2222", 1), ("2026-10-07", "2222", 6),
                           ("2026-10-07", "3333", 1), ("2026-10-07", "3333", 3), ("2026-10-07", "4444", 0)])
        e = date(2026, 10, 8)
        self.assertEqual(n.clause_type(db, "1111", e), "一")
        self.assertEqual(n.clause_type(db, "2222", e), "六")
        self.assertEqual(n.clause_type(db, "3333", e), "其他")
        self.assertEqual(n.clause_type(db, "4444", e), "無")           # 款 0 = 解析不出，不算
        self.assertEqual(n.clause_type(db, "9999", e), "無")
        self.assertEqual(n.clause_type(db, "1111", date(2026, 10, 6)), "無")   # 只用進場日(不含)之前的公告

    def test_clause_type_uses_latest_notice_only(self):
        db = self.make_db([("2026-10-01", "1111", 6), ("2026-10-07", "1111", 1)])
        self.assertEqual(n.clause_type(db, "1111", date(2026, 10, 8)), "一")

    def test_dev20_and_threshold(self):
        # 進場日 10/08 前連續 19 個交易日收盤都是 100，進場日收盤 c：乖離 = c/((1900+c)/20)-1
        days, cur = [], date(2026, 10, 8)
        for _ in range(19):
            cur = n.prev_trading_day(cur)
            days.append(cur)
        daily = {("1111", d): (1000.0, 100.0) for d in days}
        di = n.dev20_info("1111", date(2026, 10, 8), date(2026, 10, 8), {}, daily, now_price=130.0)
        self.assertAlmostEqual(di["dev"], 130 / ((1900 + 130) / 20) - 1)
        thr = di["thr"]                                            # 收盤超過它 → 乖離 > 35%
        self.assertGreater(thr / ((1900 + thr) / 20) - 1, DEV - 0.001)
        self.assertLess((thr - 0.01) / ((1900 + thr - 0.01) / 20) - 1, DEV)
        self.assertIsNone(n.dev20_info("1111", date(2026, 10, 8), date(2026, 10, 8), {}, {}, now_price=100.0))

    def test_judge(self):
        hot, cool = dict(dev=0.40, thr=1), dict(dev=0.20, thr=1)
        self.assertEqual(n.judge("一", hot), "skip")
        self.assertEqual(n.judge("一", cool), "cond")
        self.assertEqual(n.judge("一", None), "cond")
        self.assertEqual(n.judge("其他", hot), "ok")               # 只有第一款才濾
        self.assertEqual(n.judge("六", hot), "ok")

    def test_candidate_and_message_render(self):
        c = dict(code="1111", name="測試", market="上市", close=100.0, enter_freq="2分初犯", offense="初犯", days=5,
                 min_needed=1, progress={"rule1": {"current": 2, "target": 3}}, conditions=["[1] 6日漲幅 > 32% 目標 50.6"])
        mis = {"1111": dict(price=120.0, up=121.0, prev=110.0, down=99.0)}
        for clause, di, expect in (("一", dict(dev=0.40, thr=130.0, close=120.0), "不買"),
                                    ("一", dict(dev=0.20, thr=130.0, close=120.0), "≤ 130"),
                                    ("其他", dict(dev=0.20, thr=130.0, close=120.0), "可買")):
            txt, locked, skipped = n.fmt_candidate(c, mis, clause, di)
            self.assertIn(expect, txt)
            self.assertEqual(skipped, clause == "一" and di["dev"] > DEV)
        msg = n.build_pre_message(date(2026, 10, 12), date(2026, 10, 8), [c], 0, [], [], mis, "",
                                  {"1111": ("其他", dict(dev=0.2, thr=130.0, close=120.0))})
        self.assertIn("今天收盤前可買", msg)
        self.assertEqual(msg.count("<blockquote"), msg.count("</blockquote>"))


    def test_evening_candidates_message(self):
        c = dict(code="3265", name="台星科", market="上櫃", conditions=["[1-1] 6日漲幅 > 23% 目標 231.50 必進處置"])
        txt = n.fmt_next_cand(c, "其他", dict(dev=None, thr=300.0, close=None), 262.0, True)
        self.assertIn("今收 262（漲停）", txt)
        self.assertIn("可買", txt)                                  # 今天漲停不影響明天買不買
        self.assertNotIn("買不到", txt)
        hot = n.fmt_next_cand(c, "一", dict(dev=None, thr=300.0, close=None), 262.0, False)
        self.assertIn("明日收盤 ≤ 300", hot)
        msg = n.build_message(date(2026, 10, 12), date(2026, 10, 13), [], [], [], [], 0, set(),
                              [(c, "其他", None, 262.0, True)], 1)
        self.assertIn("明日 10/13 收盤前買進", msg)
        self.assertIn("3265", msg)
        self.assertEqual(msg.count("<blockquote"), msg.count("</blockquote>"))
        none = n.build_message(date(2026, 10, 12), date(2026, 10, 13), [], [], [], [], 0, set(), [], 0)
        self.assertIn("預買候選（5 天初犯）｜沒有", none)


    def test_intraday_exit_today_shows_entry_stop_profit(self):
        d = dict(code="3094", name="聯傑", market="上市", start=date(2026, 10, 8), end=date(2026, 10, 14), n_days=5)
        rr = dict(entry=69.2, stop_price=63.6)
        msg = n.build_pre_message(date(2026, 10, 14), date(2026, 10, 13), [], 0, [], [(d, {}, rr, {"price": 70.6})], {}, "", {})
        self.assertIn("盤中提醒", msg)
        self.assertNotIn("盤前", msg.replace("收盤前", ""))
        self.assertIn("進 69.2", msg)
        self.assertIn("停損價 63.6", msg)
        self.assertIn("+2.0%", msg)
        self.assertIn("今天收盤賣", msg)


    def test_futures_suffix_in_names(self):
        from unittest import mock
        import core.futures_stocks as fs
        with mock.patch.object(fs, "has_futures", side_effect=lambda c: c == "2330"):
            d = dict(code="2330", name="台積電", market="上市", start=date(2026, 10, 8), end=date(2026, 10, 14), n_days=5)
            rr = dict(entry=1000.0, stop_price=920.0)
            msg = n.build_pre_message(date(2026, 10, 14), date(2026, 10, 13), [], 0, [], [(d, {}, rr, {"price": 1010.0})], {}, "", {})
            self.assertIn("台積電(期)", msg)
            self.assertEqual(n.nm("3094", "聯傑"), "聯傑")
            self.assertEqual(n.nm("2330", "台積電(期)"), "台積電(期)")      # 不重複加


    def test_drop_driven_not_bought(self):
        # 跌太多才會處置的不買(2026-10-16)：前 6 日累積下跌 → drop，優先於乖離濾網
        self.assertEqual(n.judge("其他", dict(dev=0.1, thr=1.0, pre6=-0.05)), "drop")
        self.assertEqual(n.judge("一", dict(dev=0.5, thr=1.0, pre6=-0.2)), "drop")
        self.assertEqual(n.judge("其他", dict(dev=0.1, thr=1.0, pre6=0.08)), "ok")
        c = dict(code="3230", name="錦明", market="上櫃", conditions=[])
        txt = n.fmt_next_cand(c, "一", dict(dev=None, thr=53.66, close=None, pre6=-0.37), 26.3, False)
        self.assertIn("不買", txt)
        self.assertIn("跌", txt)
        days, cur = [], date(2026, 10, 8)
        for _ in range(19):
            cur = n.prev_trading_day(cur)
            days.append(cur)
        daily = {("1111", d): (1000.0, 100.0 + i) for i, d in enumerate(days)}   # 越往前越貴 = 一路下跌
        di = n.dev20_info("1111", date(2026, 10, 8), date(2026, 10, 8), {}, daily, now_price=95.0)
        self.assertLess(di["pre6"], 0)


DEV = 0.35


if __name__ == "__main__":
    unittest.main()
