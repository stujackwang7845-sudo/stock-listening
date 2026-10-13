r"""
CBDatabase - 可轉債（CB）資料讀取模組

資料來源：E:\Vibe Coding\CB\SummaryList\cb_data.csv
每次啟動時讀取，快取在記憶體中，不依賴網路。

[2026-10-14] 加入下櫃日：cb_data.csv 沒有下櫃日，提前全數轉換/贖回而下櫃的 CB 會被當成到期日前都在外流通
(6672 的 66721、66722 已於 2026-09-01 下櫃卻顯示 (CB))。若找得到 CB_Enterprise 資料庫
(DISPO_CB_DB_PATH，預設 E:\Vibe Coding\Stock\DB\data\cb_enterprise.db)，用 cb_master.delisting_date 截斷：
下櫃日(含)以後不算在外流通。找不到資料庫時行為與舊版相同。
"""

import csv
import os
from datetime import datetime
from typing import Dict, List, Optional


# CB CSV 的固定路徑（CB SummaryList 專案的輸出檔案）
from core.runtime import get_paths
CB_CSV_PATH = get_paths().cb_csv
CB_DB_PATH = os.environ.get("DISPO_CB_DB_PATH", r"E:\Vibe Coding\Stock\DB\data\cb_enterprise.db")


def load_delisting_dates(db_path: str = CB_DB_PATH) -> Dict[str, datetime]:
    """{cb_id: 下櫃日}；資料庫不存在或讀取失敗回傳空 dict。"""
    out: Dict[str, datetime] = {}
    if not db_path or not os.path.exists(db_path):
        return out
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for cb_id, d in conn.execute("SELECT cb_id, delisting_date FROM cb_master "
                                         "WHERE delisting_date IS NOT NULL AND delisting_date <> ''"):
                try:
                    out[str(cb_id).strip()] = datetime.strptime(str(d)[:10], "%Y-%m-%d")
                except ValueError:
                    continue
        finally:
            conn.close()
    except Exception as e:
        print(f"[CBDatabase] 讀取下櫃日失敗(略過)：{e}")
    return out


class CBDatabase:
    """
    可轉債資料庫，從 cb_data.csv 讀取資料。
    
    提供兩個主要查詢：
    - has_cb_now(stock_code): 目前是否有 CB 在外流通
    - had_cb_on(stock_code, date_str): 指定日期是否有 CB 在外流通
    """

    def __init__(self, csv_path: str = CB_CSV_PATH, db_path: str = CB_DB_PATH):
        """
        初始化並載入 CB 資料。
        
        Args:
            csv_path: cb_data.csv 的路徑
        """
        # 格式：{stock_code: [{'listing': datetime, 'maturity': datetime}, ...]}
        self._data: Dict[str, List[dict]] = {}
        self._loaded = False
        self._delist = load_delisting_dates(db_path)
        self._load(csv_path)

    def _load(self, csv_path: str) -> None:
        """讀取 cb_data.csv 並快取所有記錄。"""
        if not os.path.exists(csv_path):
            print(f"[CBDatabase] 警告：找不到 CB 資料檔案：{csv_path}")
            return

        try:
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = str(row.get("stock_code", "")).strip()
                    if not code:
                        continue

                    listing_str = row.get("listing_date", "").strip()
                    maturity_str = row.get("maturity_date", "").strip()

                    if not listing_str or not maturity_str:
                        continue

                    try:
                        listing_dt = datetime.strptime(listing_str, "%Y/%m/%d")
                        maturity_dt = datetime.strptime(maturity_str, "%Y/%m/%d")
                    except ValueError:
                        continue

                    if code not in self._data:
                        self._data[code] = []

                    cb_id = row.get("cb_id", "").strip()
                    self._data[code].append({
                        "listing": listing_dt,
                        "maturity": maturity_dt,
                        "cb_id": cb_id,
                        "delisted": self._delist.get(cb_id),   # 下櫃日(含)以後不算在外流通
                    })

            self._loaded = True
            print(f"[CBDatabase] 已載入 {len(self._data)} 支有 CB 的股票（共 {sum(len(v) for v in self._data.values())} 筆記錄）")

        except Exception as e:
            print(f"[CBDatabase] 讀取失敗：{e}")

    @staticmethod
    def _alive(entry: dict, when: datetime) -> bool:
        """這檔 CB 在 when 這天是否在外流通：發行日 ≤ when ≤ 到期日，且還沒下櫃。"""
        if not (entry["listing"] <= when <= entry["maturity"]):
            return False
        d = entry.get("delisted")
        return d is None or when < d

    def has_cb_now(self, stock_code: str) -> bool:
        """
        判斷某股票目前是否有 CB 在外流通。
        
        Args:
            stock_code: 4 碼股票代號（例如 '1815'）
        
        Returns:
            True 表示目前有 CB 在外流通
        """
        code = str(stock_code).strip()
        if code not in self._data:
            return False

        today = datetime.today()
        return any(self._alive(entry, today) for entry in self._data[code])

    def had_cb_on(self, stock_code: str, date_str: str) -> bool:
        """
        判斷某股票在指定日期時是否有 CB 在外流通。
        
        Args:
            stock_code: 4 碼股票代號（例如 '1815'）
            date_str: 日期字串（格式：'2021-04-01' 或 '2021/04/01'）
        
        Returns:
            True 表示在指定日期有 CB 在外流通
        """
        code = str(stock_code).strip()
        if code not in self._data:
            return False

        # 支援多種日期格式
        target_dt: Optional[datetime] = None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                target_dt = datetime.strptime(date_str.strip(), fmt)
                break
            except ValueError:
                continue

        if target_dt is None:
            return False

        return any(self._alive(entry, target_dt) for entry in self._data[code])

    def get_cb_count(self, stock_code: str) -> int:
        """
        取得某股票目前在外流通的 CB 數量。
        
        Returns:
            CB 數量（0 表示沒有）
        """
        code = str(stock_code).strip()
        if code not in self._data:
            return 0

        today = datetime.today()
        return sum(1 for entry in self._data[code] if self._alive(entry, today))
