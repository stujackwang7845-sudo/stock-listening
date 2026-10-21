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

    def test_drop_driven_not_bought(self):
        # 跌太多才會處置的不買(2026-10-16)：看注意公告原文是不是跌幅/第十一款，不看股價 6 日漲跌
        import sqlite3, tempfile
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE attention_clauses (announce_date TEXT, code TEXT, clause_number INT, reason TEXT)")
        c.executemany("INSERT INTO attention_clauses VALUES (?,?,?,?)", [
            ("2026-10-15", "2059", 1, "最近六個營業日累積收盤價跌幅達28.57%。且…﹝第一款﹞。"),
            ("2026-10-15", "8111", 6, "當日本益比為71.93、…當日週轉率達6.94%(第六款)"),
            ("2026-10-15", "6278", 1, "最近六個營業日累積收盤價漲幅達27.14%。﹝第一款﹞"),
            ("2026-10-13", "2059", 11, "…當日收盤價為最近六個營業日收盤價最低者﹝第十一款﹞"),
            ("2026-10-14", "9999", 11, "…﹝第十一款﹞")])
        c.commit(); c.close()
        self.assertEqual(n.drop_notice(path, "2059", "2026-10-15"), "10/15 第一款跌幅達28.57%")
        self.assertIsNone(n.drop_notice(path, "8111", "2026-10-15"))          # 第六款進處置，不是跌
        self.assertIsNone(n.drop_notice(path, "6278", "2026-10-15"))          # 漲幅
        self.assertIsNone(n.drop_notice(path, "9999", "2026-10-15"))       # 第十一款不算(可能是漲)
        self.assertIsNone(n.drop_notice(path, "2059", "2026-10-15", inclusive=False) and None)
        self.assertEqual(n.judge("其他", dict(dev=0.1, thr=1.0), "10/15 跌幅達28.57%"), "drop")
        self.assertEqual(n.judge("其他", dict(dev=0.1, thr=1.0), None), "ok")
        cc = dict(code="2059", name="川湖", market="上市", conditions=[])
        txt = n.fmt_next_cand(cc, "一", dict(dev=None, thr=1.0, close=None), 9720.0, False, "10/15 跌幅達28.57%")
        self.assertIn("不買", txt)
        self.assertIn("跌幅達28.57%", txt)

class NewPushLayoutTest(unittest.TestCase):
    """2026-10-16 新推播：策略說明 → 焦點(做多/放空/出場/停損) → 持有中；7 天初犯結束日前一天賣並反手放空(策略 A)。"""

    def test_enrich_dates(self):
        five = n.enrich(dict(code="1", name="x", start=date(2026, 10, 12), end=date(2026, 10, 16), n_days=5, offense="初犯",
                             entry=date(2026, 10, 8)))
        self.assertEqual(five["long_exit"], date(2026, 10, 16))
        self.assertNotIn("short_entry", five)
        seven = n.enrich(dict(code="2", name="y", start=date(2026, 10, 8), end=date(2026, 10, 19), n_days=7, offense="累犯",
                              entry=date(2026, 10, 8)))
        self.assertEqual(seven["long_exit"], date(2026, 10, 16))      # 結束日前一天(10/17、18 週末)
        self.assertEqual(seven["short_entry"], date(2026, 10, 16))
        self.assertEqual(seven["short_cover"], date(2026, 10, 21))    # 7 天累犯：出關日+1(10/20 出關日的隔一個交易日)
        self.assertEqual(n.cover_lbl(seven), "出關日+1")
        first = n.enrich(dict(code="3", name="z", start=date(2026, 10, 8), end=date(2026, 10, 19), n_days=7, offense="初犯",
                              entry=date(2026, 10, 8)))
        self.assertEqual(first["short_cover"], date(2026, 10, 20))    # 7 天初犯：出關日
        self.assertEqual(n.cover_lbl(first), "出關日")

    def test_lines_and_messages(self):
        from unittest import mock
        import core.futures_stocks as fs
        d7 = n.enrich(dict(code="2330", name="台積電", market="上市", start=date(2026, 10, 8), end=date(2026, 10, 19),
                           n_days=7, offense="初犯", entry=date(2026, 10, 8)))
        rr = dict(entry=1000.0, entry_date=date(2026, 10, 8), cur=1050.0, raw=5.0, adj=5.0, suspicious=False, stop_price=920.0)
        sr = dict(entry=1050.0, cur=1000.0, ret=4.76)
        with mock.patch.object(fs, "has_futures", side_effect=lambda c: c == "2330"):
            ll = n.long_card(d7, rr)
            sl = n.short_card(d7, sr)
        self.assertIn("台積電(期)", ll)
        self.assertIn("停損價：920", ll)
        self.assertIn("預計出場：10/16 收盤賣", ll)
        self.assertIn("預計回補：10/20 收盤買回", sl)
        self.assertIn("\n", ll)                                      # 一檔多行
        msg = n.build_message(date(2026, 10, 15), date(2026, 10, 16), ["多A", "多A2"], ["空B"], ["賣C"], ["補D"], [],
                              [ll], [sl], [])
        for t in ("策略說明", "明日焦點｜明天（10/16）收盤前", "做多・買進｜明天（10/16）收盤", "放空・融券賣出",
                  "賣出多單", "回補空單", "持有多單", "持有空單"):
            self.assertIn(t, msg)
        self.assertIn("多A\n" + n.SEP + "\n多A2", msg)                 # 每檔之間有分隔線
        self.assertIn("<blockquote expandable>🟢 <b>做多・買進｜明天（10/16）收盤</b>（2 檔）", msg)   # 各區塊可展開
        self.assertIn("<blockquote expandable>🔵 <b>回補空單・買回｜明天（10/16）收盤</b>（1 檔）", msg)
        self.assertEqual(msg.count("<blockquote"), msg.count("</blockquote>"))
        self.assertNotIn("明天收盤", msg.replace("RULES", ""))       # 「明天」後面一律帶日期
        pre = n.build_pre_message(date(2026, 10, 16), date(2026, 10, 15), [], [], [], [], ["停D"], [], [], [])
        self.assertIn("盤中提醒", pre)
        self.assertIn("今天（10/16）收盤前要做的事", pre)
        self.assertIn("⛔ <b>停損注意", pre)
        self.assertIn("🟢 <b>做多・買進｜今天（10/16）收盤</b>：無", pre)          # 沒內容只留一行

    def test_candidate_shows_predicted_days(self):
        c = dict(code="3265", name="台星科", market="上櫃", offense="初犯", days=5,
                 days_note="10日內6次 → 5天", conditions=["收盤漲幅 ≥ 3.2%"])
        txt = n.fmt_next_cand(c, "六", None, 100.0, False, when="明天（10/16）", today_lbl="今天（10/15）")
        self.assertIn("明天（10/16）收盤前：買進（做多）", txt)
        self.assertIn("今天（10/15）收盤：100", txt)
        self.assertIn("<b>5 天</b>", txt)
        self.assertIn("10日內6次 → 5天", txt)
        self.assertIn("收盤漲幅 ≥ 3.2%", txt)

    def test_split_message_keeps_items(self):
        items = [f"項目{i}\n" + "x" * 300 for i in range(30)]
        msg = n.build_message(date(2026, 10, 15), date(2026, 10, 16), items, [], [], [], [], [], [], [])
        chunks = n.split_message(msg)
        self.assertTrue(len(chunks) > 1 and all(len(ch) <= 4000 for ch in chunks))
        self.assertEqual(sum(ch.count("項目") for ch in chunks), 30)
        for ch in chunks:
            self.assertEqual(ch.count("<blockquote"), ch.count("</blockquote>"))   # 拆開的區塊各自補上開關標籤
        self.assertTrue(any("（續）" in ch for ch in chunks[1:]))

    def test_watch_card_for_7day_candidate(self):
        c = dict(code="6217", name="中探針", market="上櫃", offense="初犯", days=7, days_note="連5日 期間有第十三款")
        txt = n.watch_card(c, "明天（10/19）", "後天（10/20）")
        self.assertIn("明天（10/19）不動作", txt)
        self.assertIn("後天（10/20）（處置首日）收盤買進", txt)
        msg = n.build_message(date(2026, 10, 16), date(2026, 10, 19), [], [], [], [], [], [], [], [], f_watch=[txt])
        self.assertIn("👀 <b>觀察（7 天聽牌，明天（10/19）不動作）</b>（1 檔）", msg)
        c2 = dict(c, offense="累犯")
        self.assertIn("累犯不做多", n.watch_card(c2, "明天（10/19）", "後天（10/20）"))

    def test_positions_keeps_short_on_long_exit_day(self):
        """7 天初犯：同一收盤賣多單再放空，那天的持倉要有空單(2026-10-18 6213 漏掉的回歸測試)。"""
        from unittest import mock
        d = n.enrich(dict(code="6213", name="聯茂", market="上市", start=date(2026, 10, 8), end=date(2026, 10, 19),
                          n_days=7, offense="初犯", entry=date(2026, 10, 8)))
        rr = dict(entry=806.0, entry_date=date(2026, 10, 8), cur=774.0, raw=-4.0, adj=-4.0, suspicious=False,
                  stop_price=741.0, stopped=None)
        sr = dict(entry=774.0, cur=774.0, ret=0.0, blocked=False)
        with mock.patch.object(n, "long_bought", return_value=(True, "")), \
                mock.patch.object(n, "long_status", return_value=rr), \
                mock.patch.object(n, "short_status", return_value=sr):
            longs, stops, notes, shorts = n.positions([d], date(2026, 10, 16), None, {}, {}, {})
        self.assertEqual(longs, [])                                   # 多單今天收盤已賣
        self.assertEqual([x[0]["code"] for x in shorts], ["6213"])    # 空單今天收盤已空

    def test_apply_chain_extends_exit_to_second_disposal_exit(self):
        """接力處置(3287)：5 天初犯期間又公告 5 天累犯 → 多單出場改到第二次的出關日；7 天或期間外公告不改。"""
        d1 = n.enrich(dict(code="3287", name="廣寰科", market="上櫃", announce=date(2026, 10, 16), start=date(2026, 10, 19),
                           end=date(2026, 10, 23), n_days=5, offense="初犯", entry=date(2026, 10, 16)))
        d2 = n.enrich(dict(code="3287", name="廣寰科", market="上櫃", announce=date(2026, 10, 21), start=date(2026, 10, 22),
                           end=date(2026, 10, 29), n_days=5, offense="累犯", entry=date(2026, 10, 21)))
        n.apply_chain([d1, d2])
        self.assertEqual(d1["long_exit"], n.next_trading_day(date(2026, 10, 29)))
        self.assertIn("接力處置", n.chain_note(d1))
        e1 = n.enrich(dict(code="1111", name="x", market="上市", announce=date(2026, 10, 16), start=date(2026, 10, 19),
                           end=date(2026, 10, 23), n_days=5, offense="初犯", entry=date(2026, 10, 16)))
        e7 = n.enrich(dict(code="1111", name="x", market="上市", announce=date(2026, 10, 21), start=date(2026, 10, 22),
                           end=date(2026, 11, 2), n_days=7, offense="累犯", entry=date(2026, 10, 22)))
        late = n.enrich(dict(code="2222", name="y", market="上市", announce=date(2026, 10, 16), start=date(2026, 10, 19),
                             end=date(2026, 10, 23), n_days=5, offense="初犯", entry=date(2026, 10, 16)))
        late2 = n.enrich(dict(code="2222", name="y", market="上市", announce=date(2026, 10, 26), start=date(2026, 10, 27),
                              end=date(2026, 11, 2), n_days=5, offense="累犯", entry=date(2026, 10, 26)))
        n.apply_chain([e1, e7, late, late2])
        self.assertEqual(e1["long_exit"], date(2026, 10, 23))        # 第二次是 7 天：不改
        self.assertEqual(late["long_exit"], date(2026, 10, 23))      # 第一次結束後才公告：不是接力
        self.assertNotIn("chain", e1)

    def test_short_card_explains_date(self):
        d = n.enrich(dict(code="6213", name="聯茂", market="上市", start=date(2026, 10, 8), end=date(2026, 10, 19),
                          n_days=7, offense="初犯", entry=date(2026, 10, 8)))
        self.assertIn("處置 10/08～10/19：10/16 是結束日前一天，10/20（出關日）回補", n.short_why(d))

    def test_fmt_next_cand_drop(self):
        c = dict(code="2059", name="川湖", market="上市", conditions=[])
        txt = n.fmt_next_cand(c, "一", dict(dev=None, thr=1.0, close=None), 9720.0, False, "10/15 第一款跌幅達28.57%")
        self.assertIn("不買", txt)
        self.assertNotIn("預估", txt)                                # 濾掉的只留名稱＋原因兩行
        self.assertEqual(len(txt.split("\n")), 2)


DEV = 0.35


if __name__ == "__main__":
    unittest.main()
