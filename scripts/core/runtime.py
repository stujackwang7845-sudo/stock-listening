"""
執行環境接縫：資料檔路徑與外部 API 開關的唯一來源。

桌面版：不設任何環境變數，路徑與原本各檔寫死的值完全相同(相對路徑以專案根目錄為 CWD)，
行為不變。

雲端(GitHub Actions)：
  DISPO_DATA_DIR=<目錄>          所有資料檔(原本分散在 data/ 與專案根目錄)都改放這個目錄
  DISPO_NO_SHIOAJI=1             不連 StockGateway(127.0.0.1:8080)
  DISPO_NO_FINMIND=1             不建立 FinMind 連線(沒有 token 也不會崩潰)
  DISPO_STOP_SHORT_PATH=<檔案>   最後回補日 stop_short.json(預設讀股期套利專案)
  DISPO_CB_CSV_PATH=<檔案>       CB 清單 cb_data.csv(預設讀 CB SummaryList 專案)
"""
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_DEFAULT_STOP_SHORT = r"e:\Vibe Coding\Stock\股期套利\data\stop_short.json"
_DEFAULT_CB_CSV = r"E:\Vibe Coding\CB\SummaryList\cb_data.csv"


def env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def no_shioaji():
    return env_flag("DISPO_NO_SHIOAJI")


def no_finmind():
    return env_flag("DISPO_NO_FINMIND")


def data_dir_override():
    """有設 DISPO_DATA_DIR 才回傳該目錄，否則 None(桌面版)。"""
    d = os.environ.get("DISPO_DATA_DIR", "").strip()
    return d or None


@dataclass(frozen=True)
class DataPaths:
    disposal_db: str
    cache_db: str
    market_db: str
    prices_db: str            # 專案根目錄 stock_prices.db(正式股價庫，統計頁使用)
    prices_db_data: str       # data/stock_prices.db(PriceDatabase 的舊預設值，桌面版沿用)
    listening_json: str       # 桌面版由 HistoryManager._resolve_path 決定，這欄只在雲端使用
    margin_futures_db: str
    tags_json: str
    twse_holidays_json: str
    stop_short_json: str
    cb_csv: str

    @classmethod
    def from_env(cls):
        stop_short = os.environ.get("DISPO_STOP_SHORT_PATH", _DEFAULT_STOP_SHORT)
        cb_csv = os.environ.get("DISPO_CB_CSV_PATH", _DEFAULT_CB_CSV)
        d = data_dir_override()
        if not d:
            return cls(
                disposal_db="data/disposal_history.db",
                cache_db="data/cache.db",
                market_db="data/market_data.db",
                prices_db="stock_prices.db",
                prices_db_data="data/stock_prices.db",
                listening_json="data/listening_history.json",
                margin_futures_db=str(PROJECT_ROOT / "data" / "margin_futures.db"),
                tags_json="data/tags_config.json",
                twse_holidays_json="data/twse_holidays.json",
                stop_short_json=stop_short,
                cb_csv=cb_csv,
            )

        def j(name):
            return os.path.join(d, name)

        return cls(
            disposal_db=j("disposal_history.db"),
            cache_db=j("cache.db"),
            market_db=j("market_data.db"),
            prices_db=j("stock_prices.db"),
            prices_db_data=j("stock_prices.db"),
            listening_json=j("listening_history.json"),
            margin_futures_db=j("margin_futures.db"),
            tags_json=j("tags_config.json"),
            twse_holidays_json=j("twse_holidays.json"),
            stop_short_json=stop_short,
            cb_csv=cb_csv,
        )


def get_paths():
    """每次呼叫都重讀環境變數(便宜)，測試可在執行中切換。"""
    return DataPaths.from_env()
