"""CB 下櫃日判斷(2026-10-14)：6672 的 CB 已下櫃卻顯示 (CB)。全部用暫存檔，不連網。
執行：cd 專案根目錄 && uv run python -m unittest tests.test_cb_delisting -v"""
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from core.cb_data import CBDatabase  # noqa: E402


class CBDelistTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.csv = os.path.join(d, "cb.csv")
        with open(self.csv, "w", encoding="utf-8-sig") as f:
            f.write("stock_code,cb_id,listing_date,maturity_date\n")
            f.write("6672,66721,2026/01/22,2031/01/22\n")
            f.write("6672,66722,2026/02/03,2031/02/03\n")
            f.write("1111,11111,2026/01/01,2031/01/01\n")
        self.db = os.path.join(d, "cb.db")
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE cb_master (cb_id TEXT, delisting_date TEXT)")
        c.executemany("INSERT INTO cb_master VALUES (?,?)", [("66721", "2026-09-01"), ("66722", "2026-09-01"), ("11111", None)])
        c.commit()
        c.close()

    def test_delisted_not_outstanding(self):
        db = CBDatabase(self.csv, self.db)
        self.assertFalse(db.has_cb_now("6672"))
        self.assertEqual(db.get_cb_count("6672"), 0)
        self.assertTrue(db.had_cb_on("6672", "2026-08-31"))      # 下櫃前一天還在
        self.assertFalse(db.had_cb_on("6672", "2026-09-01"))     # 下櫃日當天起不算
        self.assertTrue(db.has_cb_now("1111"))                   # 沒下櫃的照舊

    def test_without_db_same_as_before(self):
        db = CBDatabase(self.csv, os.path.join(tempfile.mkdtemp(), "none.db"))
        self.assertTrue(db.has_cb_now("6672"))                   # 找不到資料庫：舊行為(只看到期日)


if __name__ == "__main__":
    unittest.main()
