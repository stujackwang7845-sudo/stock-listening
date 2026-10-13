r"""
CBDatabase - 可轉債（CB）資料讀取模組

資料來源：E:\Vibe Coding\CB\SummaryList\cb_data.csv
每次啟動時讀取，快取在記憶體中，不依賴網路。

[2026-10-14] 加入下櫃日：cb_data.csv 沒有下櫃日，提前全數轉換/贖回而下櫃的 CB 會被當成到期日前都在外流通
(6672 的 66721、66722 已於 2026-09-01 下櫃卻顯示 (CB))。若找得到 CB_Enterprise 資料庫
(DISPO_CB_DB_PATH，預設 E:\Vibe Coding\Stock\DB\data\cb_enterprise.db)，用 cb_master.delisting_date 截斷：
下櫃日(含)以後不算在外流通。找不到資料庫時行為與舊版相同。

[2026-10-14] 雲端沒有 cb_data.csv(SummaryList 是私有倉庫)：改抓櫃買中心「可轉換公司債發行資料」
(convSearch bondIssuer CSV)，那份只列目前在外流通的 CB(已下櫃的不在上面)，沒有到期日，
所以只能正確回答「現在有沒有」；had_cb_on 對已下櫃的舊 CB 會漏(雲端歷史統計的已知限制)。
"""

import csv
import os
from datetime import datetime
from typing import Dict, List, Optional


# CB CSV 的固定路徑（CB SummaryList 專案的輸出檔案）
from core.runtime import get_paths
CB_CSV_PATH = get_paths().cb_csv
CB_DB_PATH = os.environ.get("DISPO_CB_DB_PATH", r"E:\Vibe Coding\Stock\DB\data\cb_enterprise.db")


def _ymd(x) -> Optional[datetime]:
    try:
        return datetime.strptime(str(x)[:10], "%Y-%m-%d") if x else None
    except ValueError:
        return None


def load_cb_master(db_path: str = CB_DB_PATH) -> Dict[str, dict]:
    """{cb_id: {'delisted': 下櫃日或 None, 'maturity': 到期日或 None}}；資料庫不存在或讀取失敗回傳空 dict。"""
    out: Dict[str, dict] = {}
    if not db_path or not os.path.exists(db_path):
        return out
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for cb_id, d, mat in conn.execute("SELECT cb_id, delisting_date, maturity_date FROM cb_master"):
                out[str(cb_id).strip()] = {"delisted": _ymd(d), "maturity": _ymd(mat)}
        finally:
            conn.close()
    except Exception as e:
        print(f"[CBDatabase] 讀取 CB_Enterprise 下櫃日/到期日失敗(略過)：{e}")
    return out


def load_delisting_dates(db_path: str = CB_DB_PATH) -> Dict[str, datetime]:
    """{cb_id: 下櫃日}(相容舊介面)。"""
    return {k: v["delisted"] for k, v in load_cb_master(db_path).items() if v["delisted"]}


_OFFICIAL_CACHE: Optional[list] = None


def fetch_official_outstanding() -> list:
    """櫃買中心可轉換公司債發行資料(目前在外流通)：[(股票代號, CB 代號, 上櫃日)]；失敗回傳空 list。同一次執行只抓一次。"""
    global _OFFICIAL_CACHE
    if _OFFICIAL_CACHE is not None:
        return _OFFICIAL_CACHE
    out = []
    try:
        import io
        import re
        import requests
        r = requests.get("https://www.tpex.org.tw/www/zh-tw/bond/convSearch",
                         params={"name": "bondIssuer", "searchNo": "", "id": "", "response": "csv", "order": "3", "sort": "desc"},
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}, timeout=30)
        r.raise_for_status()
        text = r.content.decode("cp950", errors="replace")
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 5 or not row[0].strip().isdigit():
                continue
            code = row[0].strip()
            m = re.search(r"bond_id=(\w+)", row[4])
            y, mth, d = (int(x) for x in row[3].strip().split("/"))
            out.append((code, m.group(1) if m else "", datetime(y + 1911, mth, d)))
    except Exception as e:
        print(f"[CBDatabase] 官方 CB 清單抓取失敗：{e}")
        out = []
    _OFFICIAL_CACHE = out
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
        self._master = load_cb_master(db_path)
        self._load(csv_path)

    def _load(self, csv_path: str) -> None:
        """讀取 cb_data.csv 並快取所有記錄。"""
        if not os.path.exists(csv_path):
            print(f"[CBDatabase] 找不到 CB 資料檔案：{csv_path}，改用櫃買中心官方在外流通 CB 清單")
            self._load_official()
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
                    cb_id = row.get("cb_id", "").strip()
                    info = self._master.get(cb_id, {})

                    if not listing_str:
                        continue

                    try:
                        listing_dt = datetime.strptime(listing_str, "%Y/%m/%d")
                        if maturity_str:
                            maturity_dt = datetime.strptime(maturity_str, "%Y/%m/%d")
                        else:
                            # SummaryList 雲端版偶爾抓不到到期日(2026-10 有 86 筆空白)：CB_Enterprise 有就補，
                            # 沒有就當作仍在外流通(清單本身只列在外流通的 CB)，靠下櫃日截斷
                            maturity_dt = info.get("maturity") or datetime(2099, 12, 31)
                    except ValueError:
                        continue

                    if code not in self._data:
                        self._data[code] = []

                    self._data[code].append({
                        "listing": listing_dt,
                        "maturity": maturity_dt,
                        "cb_id": cb_id,
                        "delisted": info.get("delisted"),   # 下櫃日(含)以後不算在外流通
                    })

            self._loaded = True
            print(f"[CBDatabase] 已載入 {len(self._data)} 支有 CB 的股票（共 {sum(len(v) for v in self._data.values())} 筆記錄）")

        except Exception as e:
            print(f"[CBDatabase] 讀取失敗：{e}")

    def _load_official(self) -> None:
        rows = fetch_official_outstanding()
        for code, cb_id, listing_dt in rows:
            self._data.setdefault(code, []).append({
                "listing": listing_dt, "maturity": datetime(2099, 12, 31), "cb_id": cb_id, "delisted": None})
        self._loaded = bool(rows)
        print(f"[CBDatabase] 官方清單：{len(self._data)} 支有 CB 的股票（共 {len(rows)} 筆）")

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
