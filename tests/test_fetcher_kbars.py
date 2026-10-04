"""
fetch_stock_history 寫快取的兩個修正(2026-10-05)：
- Shioaji K棒成交量是「張」，存進快取要換成「股」
- 盤中拿到的當天未收盤 K 棒不能存(只存到最後一個已收盤交易日)
全部用假的 ShioajiClient，資料庫在暫存目錄。
執行：cd 專案根目錄 && uv run python -m unittest tests.test_fetcher_kbars -v
"""
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime
from unittest import mock

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))


class FakeShioaji:
    def __init__(self):
        self.is_connected = True

    def _ensure_connected(self):
        return True

    def get_kbars(self, code, start, end=None):
        # 10/01、10/02 已收盤；10/05 是盤中半根
        idx = pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-05"])
        return pd.DataFrame({"Open": [10, 11, 12], "High": [10, 11, 12], "Low": [10, 11, 12],
                             "Close": [10, 11, 12], "Volume": [5, 6, 7]}, index=idx).rename_axis("Date")


class FetchStockHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"DISPO_DATA_DIR": self.tmp, "DISPO_NO_FINMIND": "1"})
        self.env.start()
        fake_mod = types.ModuleType("core.shioaji_client")
        fake_mod.ShioajiClient = FakeShioaji
        self.mod = mock.patch.dict(sys.modules, {"core.shioaji_client": fake_mod})
        self.mod.start()

    def tearDown(self):
        self.mod.stop()
        self.env.stop()

    def test_volume_in_shares_and_no_intraday_bar(self):
        from core.fetcher import StockFetcher
        from core.utils import DateUtils
        f = StockFetcher(db_path=os.path.join(self.tmp, "market_data.db"))
        with mock.patch.object(DateUtils, "get_last_trading_day", return_value=datetime(2026, 10, 2)):
            df, _ = f.fetch_stock_history("2330", allow_fetch=True)
        self.assertEqual([d.strftime("%Y-%m-%d") for d in df.index], ["2026-10-01", "2026-10-02"])
        self.assertEqual(df["Volume"].tolist(), [5000.0, 6000.0])


if __name__ == "__main__":
    unittest.main()
