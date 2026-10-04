"""
MarginFuturesDatabase - 融券/期貨資訊永久資料庫

功能：
- 儲存最新的融券可交易股票清單
- 儲存最新的期貨可交易股票清單
- 每次按「更新融券/期貨」按鈕時，用最新資料覆蓋舊資料
- 表格與三個 InfoBox 讀取此 DB 確認融券/期貨狀態

DB 位置：margin_futures.db（專案根目錄）
"""

import sqlite3
from pathlib import Path
from datetime import datetime


from core.runtime import get_paths
DB_PATH = Path(get_paths().margin_futures_db)


class MarginFuturesDatabase:
    """融券/期貨永久資料庫"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        """建立資料表（若不存在）"""
        cursor = self.conn.cursor()

        # 融券可交易清單
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS margin_stocks (
                code TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL
            )
        """)

        # 期貨可交易清單
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS futures_stocks (
                code TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL
            )
        """)

        # 最後更新時間紀錄
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS update_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                update_type TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                record_count INTEGER DEFAULT 0
            )
        """)

        self.conn.commit()

    # ──── 融券相關 ──────────────────────────────────────────────────────────────

    def save_margin_stocks(self, codes: set):
        """
        覆蓋儲存最新融券清單。
        先清空舊資料，再寫入新資料，確保永遠是最新狀態。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = self.conn.cursor()

        # 清空舊資料
        cursor.execute("DELETE FROM margin_stocks")

        # 寫入新資料
        for code in codes:
            cursor.execute(
                "INSERT OR REPLACE INTO margin_stocks (code, updated_at) VALUES (?, ?)",
                (str(code).strip(), now)
            )

        # 更新日誌
        cursor.execute(
            "INSERT INTO update_log (update_type, updated_at, record_count) VALUES (?, ?, ?)",
            ("margin", now, len(codes))
        )

        self.conn.commit()
        print(f"[MarginFuturesDB] 已儲存 {len(codes)} 筆融券資料 ({now})")

    def get_margin_codes(self) -> set:
        """取得所有可融券的股票代碼集合"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT code FROM margin_stocks")
        return {row[0] for row in cursor.fetchall()}

    def is_margin_stock(self, code: str) -> bool:
        """確認股票是否可信用交易（融券）"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM margin_stocks WHERE code = ?", (str(code).strip(),))
        return cursor.fetchone() is not None

    # ──── 期貨相關 ──────────────────────────────────────────────────────────────

    def save_futures_stocks(self, codes: set):
        """
        覆蓋儲存最新股票期貨清單。
        先清空舊資料，再寫入新資料，確保永遠是最新狀態。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = self.conn.cursor()

        # 清空舊資料
        cursor.execute("DELETE FROM futures_stocks")

        # 寫入新資料
        for code in codes:
            cursor.execute(
                "INSERT OR REPLACE INTO futures_stocks (code, updated_at) VALUES (?, ?)",
                (str(code).strip(), now)
            )

        # 更新日誌
        cursor.execute(
            "INSERT INTO update_log (update_type, updated_at, record_count) VALUES (?, ?, ?)",
            ("futures", now, len(codes))
        )

        self.conn.commit()
        print(f"[MarginFuturesDB] 已儲存 {len(codes)} 筆期貨資料 ({now})")

    def get_futures_codes(self) -> set:
        """取得所有有股票期貨的股票代碼集合"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT code FROM futures_stocks")
        return {row[0] for row in cursor.fetchall()}

    def has_futures(self, code: str) -> bool:
        """
        確認股票是否有發行股票期貨。
        優先查詢 DB（最新 API 資料），若 DB 無資料則 fallback 到靜態清單。
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM futures_stocks WHERE code = ?", (str(code).strip(),))
        if cursor.fetchone() is not None:
            return True

        # Fallback：靜態清單（確保首次使用前不會漏掉）
        try:
            from core.futures_stocks import has_futures as static_check
            return static_check(code)
        except Exception:
            return False

    # ──── 狀態查詢 ──────────────────────────────────────────────────────────────

    def get_last_update_time(self) -> dict:
        """
        取得融券/期貨最近一次更新時間。
        回傳 {'margin': '2026-02-20 23:00:00', 'futures': '2026-02-20 23:00:00'}
        """
        result = {"margin": None, "futures": None}
        cursor = self.conn.cursor()

        for update_type in ("margin", "futures"):
            cursor.execute(
                "SELECT updated_at, record_count FROM update_log WHERE update_type = ? ORDER BY id DESC LIMIT 1",
                (update_type,)
            )
            row = cursor.fetchone()
            if row:
                result[update_type] = f"{row[0]} ({row[1]} 筆)"

        return result

    def has_data(self) -> bool:
        """確認 DB 是否有任何資料（用於首次啟動提示）"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM margin_stocks")
        margin_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM futures_stocks")
        futures_count = cursor.fetchone()[0]
        return margin_count > 0 or futures_count > 0

    # ──── 資源管理 ──────────────────────────────────────────────────────────────

    def close(self):
        """關閉資料庫連線"""
        if self.conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
