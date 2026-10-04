"""
股價資料庫管理系統

使用 SQLite 儲存每日股票價格數據
"""

import sqlite3
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional


class PriceDatabase:
    """股價資料庫管理類別"""
    
    def __init__(self, db_path="data/stock_prices.db"):
        """
        初始化資料庫
        
        Args:
            db_path: 資料庫檔案路徑
        """
        self.db_path = db_path
        self.conn = None
        self._init_database()
    
    def _init_database(self):
        """初始化資料庫結構"""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()
        
        # 建立股價表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,              -- YYYY-MM-DD
                code TEXT NOT NULL,              -- 股票代號
                name TEXT,                       -- 股票名稱
                open REAL,                       -- 開盤價
                high REAL,                       -- 最高價
                low REAL,                        -- 最低價
                close REAL,                      -- 收盤價
                volume INTEGER,                  -- 成交量
                change_pct REAL,                 -- 漲跌幅 %
                source TEXT,                     -- '上市' or '上櫃'
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, code)
            )
        """)
        
        # 建立索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_date ON stock_prices(date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_code ON stock_prices(code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_date_code ON stock_prices(date, code)")
        
        self.conn.commit()
        print(f"資料庫已初始化: {self.db_path}")
    
    def save_price(self, code: str, date: str, price_data: Dict) -> bool:
        """
        儲存單筆股價資料 (Wrapper for insert_daily_prices)
        
        Args:
            code: 股票代號
            date: 日期 (YYYY-MM-DD)
            price_data: 包含 close 等數據的字典
        """
        data = {
            'code': code,
            'date': date,
            'close': price_data.get('close'),
            'open': price_data.get('open'),
            'high': price_data.get('high'),
            'low': price_data.get('low'),
            'volume': price_data.get('volume'),
            'source': price_data.get('source', 'FinMind')
        }
        return self.insert_daily_prices([data]) > 0
    
    def insert_daily_prices(self, data_list: List[Dict]) -> int:
        """
        批次插入當日價格
        
        Args:
            data_list: 價格數據列表，每筆包含 date, code, name, open, high, low, close, volume, source
        
        Returns:
            成功插入的筆數
        """
        cursor = self.conn.cursor()
        inserted = 0
        
        for data in data_list:
            try:
                # 計算漲跌幅（與前一交易日比較）
                change_pct = self._calculate_change_pct(data['code'], data['date'], data['close'])
                
                cursor.execute("""
                    INSERT OR REPLACE INTO stock_prices
                    (date, code, name, open, high, low, close, volume, change_pct, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    data['date'],
                    data['code'],
                    data.get('name'),
                    data.get('open'),
                    data.get('high'),
                    data.get('low'),
                    data['close'],
                    data.get('volume'),
                    change_pct,
                    data.get('source', '未知')
                ))
                
                inserted += 1
            
            except Exception as e:
                print(f"插入失敗: {data.get('code')} - {e}")
        
        self.conn.commit()
        return inserted
    
    def _calculate_change_pct(self, code: str, date: str, close: float) -> Optional[float]:
        """計算漲跌幅"""
        if not close:
            return None
        
        # 查詢前一交易日收盤價
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT close FROM stock_prices
            WHERE code = ? AND date < ?
            ORDER BY date DESC
            LIMIT 1
        """, (code, date))
        
        result = cursor.fetchone()
        if result and result[0]:
            prev_close = float(result[0])
            change_pct = ((close - prev_close) / prev_close) * 100
            return round(change_pct, 2)
        
        return None
    
    def get_price(self, code: str, date: str) -> Optional[Dict]:
        """
        查詢特定股票特定日期的價格
        
        Args:
            code: 股票代號
            date: 日期 (YYYY-MM-DD)
        
        Returns:
            價格數據字典，若無資料則返回 None
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT date, code, name, open, high, low, close, volume, change_pct, source
            FROM stock_prices
            WHERE code = ? AND date = ?
        """, (code, date))
        
        result = cursor.fetchone()
        if result:
            return {
                'date': result[0],
                'code': result[1],
                'name': result[2],
                'open': result[3],
                'high': result[4],
                'low': result[5],
                'close': result[6],
                'volume': result[7],
                'change_pct': result[8],
                'source': result[9]
            }
        
        return None
    
    def get_price_range(self, code: str, start_date: str, end_date: str) -> List[Dict]:
        """
        查詢區間價格
        
        Args:
            code: 股票代號
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)
        
        Returns:
            價格數據列表
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT date, code, name, open, high, low, close, volume, change_pct, source
            FROM stock_prices
            WHERE code = ? AND date >= ? AND date <= ?
            ORDER BY date ASC
        """, (code, start_date, end_date))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'date': row[0],
                'code': row[1],
                'name': row[2],
                'open': row[3],
                'high': row[4],
                'low': row[5],
                'close': row[6],
                'volume': row[7],
                'change_pct': row[8],
                'source': row[9]
            })
        
        return results
    
    def get_latest_source(self, code: str) -> str:
        """取得股票最新的來源標籤 (上市/上櫃)"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT source FROM stock_prices WHERE code = ? ORDER BY date DESC LIMIT 1", (code,))
        res = cursor.fetchone()
        return res[0] if res else "上市"  # Default

    def get_statistics(self) -> Dict:
        """取得資料庫統計資訊"""
        cursor = self.conn.cursor()
        
        # 總筆數
        cursor.execute("SELECT COUNT(*) FROM stock_prices")
        total = cursor.fetchone()[0]
        
        # 各來源筆數
        cursor.execute("SELECT source, COUNT(*) FROM stock_prices GROUP BY source")
        by_source = dict(cursor.fetchall())
        
        # 不同股票數量
        cursor.execute("SELECT COUNT(DISTINCT code) FROM stock_prices")
        unique_stocks = cursor.fetchone()[0]
        
        # 日期範圍
        cursor.execute("SELECT MIN(date), MAX(date) FROM stock_prices")
        date_range = cursor.fetchone()
        
        return {
            "total_records": total,
            "by_source": by_source,
            "unique_stocks": unique_stocks,
            "date_range": {"start": date_range[0], "end": date_range[1]}
        }
        
    def get_recent_history(self, code: str, limit: int = 30, end_date: str = None) -> Optional[pd.DataFrame]:
        """
        取得最近的歷史價格數據並返回 DataFrame 格式
        
        Args:
            code: 股票代號
            limit: 限制筆數 (預設 30)
            end_date: 結束日期 (YYYY-MM-DD)，若有提供則只撈取該日期及更早的數據
        
        Returns:
            以日期的 DatetimeIndex 排序且包含 Open, High, Low, Close, Volume 的 DataFrame，若無資料則為 None
        """
        cursor = self.conn.cursor()
        query = "SELECT date, open, high, low, close, volume FROM stock_prices WHERE code = ?"
        params = [code]
        
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
            
        query += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        if not rows:
            return None
            
        # 將降冪結果反轉以符合時間順序 (舊->新)
        rows.reverse()
        
        df = pd.DataFrame(rows, columns=['date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        return df

    def close(self):
        """關閉資料庫連線"""
        if self.conn:
            self.conn.close()


if __name__ == "__main__":
    # 測試
    db = PriceDatabase("stock_prices.db")
    
    # 顯示統計
    stats = db.get_statistics()
    print(f"\n資料庫統計:")
    print(f"  總筆數: {stats['total_records']:,}")
    print(f"  各來源: {stats['by_source']}")
    print(f"  不同股票: {stats['unique_stocks']}")
    print(f"  日期範圍: {stats['date_range']['start']} ~ {stats['date_range']['end']}")
    
    db.close()
