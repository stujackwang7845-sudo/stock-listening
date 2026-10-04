"""
P1 執行環境接縫測試(core/runtime.py)。
- 桌面版(不設環境變數)：所有路徑必須跟改動前寫死的值完全相同
- 雲端(DISPO_DATA_DIR)：所有資料檔改到指定目錄
- DISPO_NO_SHIOAJI / DISPO_NO_FINMIND：不連線、不崩潰
- core 模組在沒有 PyQt6 的環境下可 import
全部在暫存目錄或子程序執行，不碰真正的資料庫。
執行：cd 專案根目錄 && uv run python -m unittest tests.test_runtime -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPTS = os.path.join(ROOT, 'scripts')

# 改動前各檔寫死的值(桌面版必須完全一致)
OLD_DESKTOP = {
    "CacheManager": "data/cache.db",
    "MarketDataCache": "data/market_data.db",
    "StockFetcher": "data/market_data.db",
    "DisposalDatabase": "data/disposal_history.db",
    "PriceDatabase": "data/stock_prices.db",
    "TagManager": "data/tags_config.json",
    "TWSE_HOLIDAYS": "data/twse_holidays.json",
    "CB_CSV": r"E:\Vibe Coding\CB\SummaryList\cb_data.csv",
    "MARGIN_DB": os.path.join(ROOT, "data", "margin_futures.db"),
    "ANALYTICS_DB": os.path.join(ROOT, "data", "disposal_history.db"),
    "STOP_SHORT": r"e:\Vibe Coding\Stock\股期套利\data\stop_short.json",
    "PRICES_ROOT": "stock_prices.db",
}

PROBE = r'''
import json, os, sys
sys.path.insert(0, SCRIPTS)
from core.cache import CacheManager
from core.market_cache import MarketDataCache
from core.fetcher import StockFetcher
from core.disposal_database import DisposalDatabase
from core.price_database import PriceDatabase
from core.tag_manager import TagManager
from core.utils import DateUtils
from core import cb_data, margin_futures_db, disposal_stats_analytics
from core.runtime import get_paths
from core.history_manager import HistoryManager
out = {
    "CacheManager": CacheManager().db_path,
    "MarketDataCache": MarketDataCache().db_path,
    "StockFetcher": StockFetcher().db_path,
    "DisposalDatabase": DisposalDatabase().db_path,
    "PriceDatabase": PriceDatabase().db_path,
    "TagManager": TagManager.FILE_PATH,
    "TWSE_HOLIDAYS": DateUtils._TWSE_CACHE_FILE,
    "CB_CSV": cb_data.CB_CSV_PATH,
    "MARGIN_DB": str(margin_futures_db.DB_PATH),
    "ANALYTICS_DB": str(disposal_stats_analytics.DEFAULT_DB_PATH),
    "STOP_SHORT": get_paths().stop_short_json,
    "PRICES_ROOT": get_paths().prices_db,
    "HISTORY": HistoryManager().FILE_PATH,
    "PYQT_LOADED": any(m.startswith("PyQt6") for m in sys.modules),
}
print("JSON:" + json.dumps(out, ensure_ascii=False))
'''

# 擋掉 PyQt6：證明 core 不依賴 GUI 套件(雲端環境不裝 PyQt6)
BLOCK_PYQT = r'''
import sys
class _Block:
    def find_spec(self, name, path=None, target=None):
        if name == "PyQt6" or name.startswith("PyQt6."):
            raise ImportError("PyQt6 blocked for test")
        return None
sys.meta_path.insert(0, _Block())
'''


def run_probe(cwd, env_extra, block_pyqt=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith("DISPO_")}
    env.update(env_extra)
    env["PYTHONIOENCODING"] = "utf-8"
    code = (BLOCK_PYQT if block_pyqt else "") + f"SCRIPTS = {SCRIPTS!r}\n" + PROBE
    r = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    line = next((l for l in r.stdout.splitlines() if l.startswith("JSON:")), None)
    if line is None:
        raise AssertionError(f"probe failed:\nSTDOUT:{r.stdout[-2000:]}\nSTDERR:{r.stderr[-3000:]}")
    return json.loads(line[5:])


class TestDesktopUnchanged(unittest.TestCase):
    def test_paths_identical_to_old_literals(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 桌面版不設路徑變數；FinMind 關掉只是避免測試連網，不影響路徑
            got = run_probe(tmp, {"DISPO_NO_FINMIND": "1", "DISPO_NO_SHIOAJI": "1"})
        for k, v in OLD_DESKTOP.items():
            self.assertEqual(got[k], v, k)


class TestCloudDataDir(unittest.TestCase):
    def test_all_files_under_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cwd:
            got = run_probe(cwd, {"DISPO_DATA_DIR": tmp, "DISPO_NO_FINMIND": "1",
                                  "DISPO_NO_SHIOAJI": "1", "DISPO_CB_CSV_PATH": "",
                                  "DISPO_STOP_SHORT_PATH": ""}, block_pyqt=True)
            for k in ["CacheManager", "MarketDataCache", "StockFetcher", "DisposalDatabase",
                      "PriceDatabase", "TagManager", "TWSE_HOLIDAYS", "MARGIN_DB",
                      "ANALYTICS_DB", "PRICES_ROOT", "HISTORY"]:
                self.assertEqual(os.path.dirname(os.path.abspath(got[k])), os.path.abspath(tmp), k)
            # core 在擋掉 PyQt6 的情況下 import 成功
            self.assertFalse(got["PYQT_LOADED"])
            # 建立的資料庫檔案都在指定目錄
            self.assertTrue(os.path.exists(os.path.join(tmp, "disposal_history.db")))
            self.assertEqual(os.listdir(cwd), [], "工作目錄不應被寫入任何檔案")


class TestApiSwitches(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, SCRIPTS)

    def test_no_finmind(self):
        os.environ["DISPO_NO_FINMIND"] = "1"
        try:
            from core.finmind_client import FinMindClient
            fm = FinMindClient()  # 沒有任何 token 也不能崩潰
            self.assertIsNone(fm.fetch_daily_price("2330", "2026-01-01"))
        finally:
            os.environ.pop("DISPO_NO_FINMIND", None)

    def test_no_shioaji(self):
        os.environ["DISPO_NO_SHIOAJI"] = "1"
        try:
            from core.shioaji_client import ShioajiClient
            c = ShioajiClient()
            self.assertFalse(c._ensure_connected())
            self.assertIsNone(c.get_kbars("2330", "2026-01-01"))
        finally:
            os.environ.pop("DISPO_NO_SHIOAJI", None)


if __name__ == "__main__":
    unittest.main()
