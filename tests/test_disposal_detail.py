"""
「詳細條件」頁核心(core/disposal_detail.py)測試，不讀正式資料庫(注入紀錄、條款查詢換成假的)。
執行：cd 專案根目錄 && uv run python -m unittest tests.test_disposal_detail -v
"""
import os
import sys
import unittest
from datetime import date
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

from core import disposal_detail as dd  # noqa: E402

TWSE_REASON = ("１處置原因：該有價證券之交易，連續三個營業日達本公司「公布注意交易資訊」標準，且該股票於最近三十個"
               "營業日內曾發布處置交易資訊。\n２處置期間：自民國一百十五年十月十二日起至一百十五年十月十六日")
TPEX_REASON = ("* 公布日期：115/10/08\n* 處置原因：\n最近30個營業日內有12個營業日達本中心「公布注意交易資訊」標準。\n\n"
               "* 處置起訖期間：\n自115年10月12日起5個營業日")


def rec(ann, ps, pe, reason=TWSE_REASON, measure="2分"):
    return {"code": "6672", "name": "騰輝電子-KY", "source": "上市", "announce_date": ann,
            "period_start": ps, "period_end": pe, "measure": measure, "reason": reason}


class ReasonTest(unittest.TestCase):
    def test_official_reason_twse_and_tpex(self):
        self.assertTrue(dd.official_reason_text(TWSE_REASON).startswith("該有價證券之交易，連續三個營業日"))
        self.assertNotIn("處置期間", dd.official_reason_text(TWSE_REASON))
        self.assertEqual(dd.official_reason_text(TPEX_REASON),
                         "最近30個營業日內有12個營業日達本中心「公布注意交易資訊」標準。")

    def test_condition_label(self):
        self.assertEqual(dd.condition_label(TWSE_REASON), ("連續3個營業日達注意標準", True))
        self.assertEqual(dd.condition_label(TPEX_REASON), ("最近30個營業日內有12個營業日達注意標準", False))


@mock.patch.object(dd, "clause13_days", lambda code, until, n, db_path=None: [])
@mock.patch.object(dd, "recent_clauses", lambda code, until, n_days=10, db_path=None: [])
class DetailTest(unittest.TestCase):
    # 6672：10/06~10/13 處置中，10/08 再公告 10/12~10/16(使用者規則：以最新公告為準)
    RECORDS = [rec("2026/10/05", "2026-10-06", "2026-10-13"), rec("2026/10/08", "2026-10-12", "2026-10-16")]

    def test_latest_announcement_wins(self):
        d = dd.compute_disposal_detail("6672", date(2026, 10, 8), records=self.RECORDS)
        self.assertEqual((d["period_start"], d["period_end"], d["exit_date"]), ("2026-10-12", "2026-10-16", "2026-10-19"))
        self.assertEqual((d["state"], d["day_no"], d["total_days"]), ("已公告，尚未生效", 0, 5))
        self.assertEqual((d["offense"], d["stats_bucket"]), ("累犯", "5天_累犯"))

    def test_before_second_announcement_shows_first(self):
        d = dd.compute_disposal_detail("6672", date(2026, 10, 7), records=self.RECORDS)
        self.assertEqual((d["period_start"], d["state"], d["day_no"], d["offense"]), ("2026-10-06", "處置中", 2, "初犯"))

    def test_after_exit_shows_outlook(self):
        # 2026-10-16 使用者要求：已出關就顯示「如果接下來進處置」的預估，不顯示舊的那次
        d = dd.compute_disposal_detail("6672", date(2026, 10, 20), records=self.RECORDS)
        self.assertEqual(d["mode"], "outlook")
        self.assertTrue(d["state"].startswith("未處置"))
        self.assertIn(d["total_days"], (5, 7))
        self.assertIn("2026-10-12 ～ 2026-10-16", d["last_disposal"])
        self.assertNotIn("period_start", d)

    def test_outlook_listening_fields(self):
        tp = {"rule1": {"current": 2, "target": 3, "needed": 1}, "min_needed": 1}
        d = dd.compute_disposal_detail("6672", date(2026, 10, 20), records=self.RECORDS,
                                       next_hint={"trigger_progress": tp, "enter_freq": "2分累犯"})
        self.assertEqual((d["freq"], d["offense"], d["state"]), ("2分", "累犯", "未處置（聽牌）"))
        self.assertTrue(d["progress"].startswith("聽牌"))

    def test_next_hint_from_forecast_is_used(self):
        # 總覽已算好的規則進度與下次進處置，詳細條件直接沿用(數字一致)
        tp = {"rule1": {"current": 2, "target": 3, "needed": 1}, "min_needed": 1}
        d = dd.compute_disposal_detail("6672", date(2026, 10, 7), records=self.RECORDS,
                                       next_hint={"trigger_progress": tp, "enter_freq": "2分累犯"})
        self.assertEqual(d["offense"], "初犯")                      # 本次處置
        self.assertEqual((d["next"]["next_label"], d["next"]["min_needed"], d["next"]["from_forecast"]),
                         ("2分累犯", 1, True))                       # 下次進處置
        # 圖表看下次進處置(使用者要求)：本次初犯，下次累犯 → 5天_累犯
        self.assertEqual((d["stats_bucket"], d["next_stats_bucket"]), ("5天_初犯", "5天_累犯"))

    def test_next_stats_uses_announced_disposal(self):
        # 已公告尚未生效：下次就是那一筆(天數、初犯累犯已知)
        d = dd.compute_disposal_detail("6672", date(2026, 10, 8), records=self.RECORDS)
        self.assertEqual((d["next_stats_bucket"], d["next_stats_title"]), ("5天_累犯", "新制 5天累犯"))

    def test_no_disposal(self):
        self.assertFalse(dd.compute_disposal_detail("2330", date(2026, 10, 8), records=self.RECORDS)["has_disposal"])


class NextDaysTest(unittest.TestCase):
    """下次處置天數：最快達標規則的計算期間內有第十三款(當沖占比過高) → 7 天(證交所 2026-08-10 新制)。"""
    TP = {"rule1": {"current": 2, "target": 3, "needed": 1}, "rule2": {"current": 2, "target": 5, "needed": 3},
          "rule3": {"current": 2, "target": 6, "needed": 4}, "rule4": {"current": 2, "target": 12, "needed": 10}}

    def test_clause13_in_window_gives_7(self):
        calls = []

        def fake(code, until, n, db_path=None):
            calls.append(n)
            return ["2026-10-08"]
        with mock.patch.object(dd, "clause13_days", fake):
            days, why = dd.predict_next_days("3441", "2026-10-08", self.TP)
        self.assertEqual(days, 7)
        self.assertEqual(calls, [2])          # 連3日還差1次 → 只看最近 3-1=2 個交易日
        self.assertIn("第十三款", why)

    def test_no_clause13_gives_5(self):
        with mock.patch.object(dd, "clause13_days", lambda code, until, n, db_path=None: []):
            self.assertEqual(dd.predict_next_days("4556", "2026-10-08", self.TP)[0], 5)


class ChartChoiceTest(unittest.TestCase):
    """圖表看哪一次(使用者規則)：聽牌才看下次；處置中未聽牌看本次；已公告未生效看那一次。"""
    BASE = {"stats_bucket": "5天_初犯", "stats_title": "新制 5天初犯",
            "next_stats_bucket": "5天_累犯", "next_stats_title": "新制 5天累犯", "next_stats_basis": ""}

    def d(self, state, need):
        return dict(self.BASE, state=state, next={"trigger_progress": {"min_needed": need}})

    def test_disposed_not_listening_shows_current(self):   # 2033：處置中、還差 3 次
        self.assertEqual(dd.chart_choice(self.d("處置中", 3))[3], "本次處置")
        self.assertEqual(dd.chart_choice(self.d("處置中", 2))[0], "5天_初犯")

    def test_disposed_listening_shows_next(self):          # 4556：處置中、再 1 次
        self.assertEqual(dd.chart_choice(self.d("處置中", 1))[:1], ("5天_累犯",))

    def test_not_disposed_shows_next(self):
        self.assertEqual(dd.chart_choice(self.d(None, 3))[3], "下次進處置")


if __name__ == "__main__":
    unittest.main()
