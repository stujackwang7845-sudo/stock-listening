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

    def test_after_exit(self):
        d = dd.compute_disposal_detail("6672", date(2026, 10, 20), records=self.RECORDS)
        self.assertEqual((d["state"], d["day_no"], d["period_end"]), ("已出關", 5, "2026-10-16"))

    def test_next_hint_from_forecast_is_used(self):
        # 總覽已算好的規則進度與下次進處置，詳細條件直接沿用(數字一致)
        tp = {"rule1": {"current": 2, "target": 3, "needed": 1}, "min_needed": 1}
        d = dd.compute_disposal_detail("6672", date(2026, 10, 7), records=self.RECORDS,
                                       next_hint={"trigger_progress": tp, "enter_freq": "2分累犯"})
        self.assertEqual(d["offense"], "初犯")                      # 本次處置
        self.assertEqual((d["next"]["next_label"], d["next"]["min_needed"], d["next"]["from_forecast"]),
                         ("2分累犯", 1, True))                       # 下次進處置

    def test_no_disposal(self):
        self.assertFalse(dd.compute_disposal_detail("2330", date(2026, 10, 8), records=self.RECORDS)["has_disposal"])


if __name__ == "__main__":
    unittest.main()
