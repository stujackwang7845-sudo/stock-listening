"""2026-10-18 本地資料補洞：股價/本益比缺日、注意條款缺日與抓取紀錄、前60日第二款處置改查本地。"""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from core import price_gapfill as pg  # noqa: E402
from core import conditions_engine as ce  # noqa: E402
import core.dashboard_engine as de  # noqa: E402


def _market_db(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE price_history (stock_id TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, "
              "volume REAL, PRIMARY KEY (stock_id, date))")
    c.execute("CREATE TABLE ratios (stock_id TEXT, date TEXT, per REAL, pbr REAL, PRIMARY KEY (stock_id, date))")
    c.commit()
    c.close()


DAYS = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)]


class PriceGapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "m.db")
        _market_db(self.db)
        pg._fm_tried.clear()
        # 交易日曆＝DAYS；parquet 有 2330 全部日子、9999 在 10/05 停牌(全市場有那天、9999 沒有)
        self.pq = {d: (1, 2, 0.5, 1.5, 1000) for d in DAYS}
        self.patches = [
            mock.patch.object(pg, "trading_days", lambda lo, hi: [d for d in DAYS if lo <= d <= hi]),
            mock.patch.object(pg, "parquet_days", lambda lo, hi: ({d for d in DAYS if lo <= d <= hi}, set())),
            mock.patch.object(pg, "parquet_rows", lambda code, days: {
                d: r for d, r in self.pq.items() if d in set(days) and not (code == "9999" and d == DAYS[2])}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_fill_only_missing_never_overwrite(self):
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO price_history VALUES ('2330', '2026-10-01', 9, 9, 9, 9, 9)")   # 既有資料
        c.execute("INSERT INTO price_history VALUES ('2330', '2026-10-07', 9, 9, 9, 9, 9)")
        c.commit()
        added, still = pg.fill_code(self.db, "2330", [DAYS[0], DAYS[4]], DAYS[0], DAYS[4])
        self.assertEqual(added, 3)
        self.assertEqual(still, [])
        rows = dict(c.execute("SELECT date, close FROM price_history WHERE stock_id='2330'").fetchall())
        self.assertEqual(rows["2026-10-01"], 9)            # 沒被覆蓋
        self.assertEqual(rows["2026-10-02"], 1.5)
        c.close()

    def test_suspended_day_is_not_missing_and_no_fetch(self):
        fm = mock.Mock()
        added, still = pg.fill_code(self.db, "9999", [DAYS[0], DAYS[4]], DAYS[0], DAYS[4], fm, allow_fetch=True)
        self.assertEqual(still, [])                         # 10/05 停牌，不算缺
        fm.fetch_daily_price.assert_not_called()

    def test_unfillable_reported_and_fetched_once(self):
        with mock.patch.object(pg, "parquet_rows", lambda code, days: {}), \
                mock.patch.object(pg, "parquet_days", lambda lo, hi: (set(), set())):
            fm = mock.Mock()
            fm.fetch_daily_price.return_value = None
            _, still = pg.fill_code(self.db, "2330", [DAYS[0]], DAYS[0], DAYS[4], fm, allow_fetch=True)
            self.assertEqual(still, DAYS[1:])
            pg.fill_code(self.db, "2330", [DAYS[0]], DAYS[0], DAYS[4], fm, allow_fetch=True)
            self.assertEqual(fm.fetch_daily_price.call_count, 1)    # 同一次執行只上網一次
        self.assertIn("10/02", pg.missing_text(still))

    def test_fill_ratios(self):
        with mock.patch.object(pg, "_load_per_year", lambda y: {("2330", DAYS[1]): (20.5, 3.1), ("2330", DAYS[2]): (None, 2.0)}):
            c = sqlite3.connect(self.db)
            c.execute("INSERT INTO ratios VALUES ('2330', '2026-10-01', 99, 99)")
            c.commit()
            n = pg.fill_ratios(self.db, "2330", DAYS[:3], DAYS[0], DAYS[4])
            self.assertEqual(n, 2)
            self.assertEqual(c.execute("SELECT per FROM ratios WHERE date='2026-10-01'").fetchone()[0], 99)
            c.close()


class C2LocalTest(unittest.TestCase):
    def test_norm_dates(self):
        self.assertEqual(ce._norm_ymd("115/07/20"), "20260720")
        self.assertEqual(ce._norm_ymd("2026/07/20"), "20260720")
        self.assertEqual(ce._norm_ymd("2026-07-20"), "20260720")
        self.assertEqual(ce._norm_ymd("20260720"), "20260720")

    def test_has_c2_requires_clause2_before_announcement(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "d.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE attention_clauses (announce_date TEXT, code TEXT, clause_number INTEGER)")
        c.execute("INSERT INTO attention_clauses VALUES ('2026-09-01', '1111', 2)")
        c.execute("INSERT INTO attention_clauses VALUES ('2026-09-01', '2222', 1)")
        c.commit()
        c.close()
        anchor = datetime(2026, 10, 15)
        recs = [{"announce_date": "115/09/03"}]
        self.assertTrue(ce.has_c2_disp_60_local("1111", recs, anchor, db))
        self.assertFalse(ce.has_c2_disp_60_local("2222", recs, anchor, db))        # 第一款不算
        self.assertFalse(ce.has_c2_disp_60_local("1111", [{"announce_date": "2026/07/01"}], anchor, db))   # 超過 60 天
        self.assertFalse(ce.has_c2_disp_60_local("1111", [], anchor, db))


class ClauseGapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "d.db")
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE attention_clauses (id INTEGER PRIMARY KEY, announce_date TEXT, code TEXT, name TEXT, "
                  "source TEXT, clause_number INTEGER, clause_desc TEXT, reason TEXT, created_at TEXT, "
                  "UNIQUE(announce_date, code, clause_number))")
        c.execute("INSERT INTO attention_clauses (announce_date, code, source, clause_number) VALUES ('2026-10-12', '1111', 'TWSE', 1)")
        c.commit()
        c.close()

    def _run(self, fetch, target, today):
        class FakeDate(date):
            @classmethod
            def today(cls):
                return today
        tdays = [date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)]
        with mock.patch("core.scraper_twse_notice.NoticeFetcher.fetch_market", side_effect=fetch), \
                mock.patch.object(de.DateUtils, "is_trading_day", side_effect=lambda d: (d.date() if hasattr(d, "date") and callable(d.date) else d) in tdays), \
                mock.patch("datetime.date", FakeDate):
            return de.fill_attention_clauses_gap(datetime.combine(target, datetime.min.time()), self.db,
                                                 lookback=3, pause=0)

    def test_failed_day_retried_and_empty_day_logged(self):
        calls = []

        def fetch(d, mk):
            calls.append((d.date(), mk))
            if d.date() == date(2026, 10, 13) and mk == "TWSE":
                return False, []                                      # 抓失敗
            if mk == "TPEX":
                return True, []                                       # 當天真的沒有
            return True, [{"code": "2222", "name": "x", "reason": "第一款", "source": "TWSE"}]
        self._run(fetch, date(2026, 10, 14), date(2026, 10, 18))
        log = dict(((a, s), st) for a, s, st in sqlite3.connect(self.db).execute(
            "SELECT announce_date, source, status FROM attention_fetch_log"))
        self.assertEqual(log[("2026-10-13", "TWSE")], "fail")
        self.assertEqual(log[("2026-10-13", "TPEX")], "ok")          # 0 筆也算完成
        # 10/12 TWSE 是舊資料(早於紀錄啟用日)→ 不重抓；TPEX 沒資料 → 要抓
        self.assertNotIn((date(2026, 10, 12), "TWSE"), calls)
        self.assertIn((date(2026, 10, 12), "TPEX"), calls)
        calls.clear()
        self._run(fetch, date(2026, 10, 14), date(2026, 10, 18))
        self.assertEqual(calls, [(date(2026, 10, 13), "TWSE")])      # 只重抓失敗的那一個

    def test_today_not_marked_done(self):
        self._run(lambda d, mk: (True, []), date(2026, 10, 14), date(2026, 10, 14))
        log = {(a, s) for a, s in sqlite3.connect(self.db).execute("SELECT announce_date, source FROM attention_fetch_log")}
        self.assertNotIn(("2026-10-14", "TWSE"), log)                # 今天可能還沒公布，不能記完成
        self.assertIn(("2026-10-13", "TWSE"), log)


if __name__ == "__main__":
    unittest.main()
