"""
新增交易日快取資料表，避免重複下載台積電資料驗證
"""
import sqlite3
from datetime import datetime

DB_PATH = "stock_prices.db"

# 連接資料庫
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# 建立交易日快取表
create_table_sql = """
CREATE TABLE IF NOT EXISTS trading_day_cache (
    date TEXT PRIMARY KEY,
    is_trading_day INTEGER NOT NULL,  -- 1: 是交易日, 0: 非交易日
    verified_at TEXT NOT NULL,         -- 驗證時間
    verification_source TEXT           -- 驗證來源 (如 "2330")
)
"""

cursor.execute(create_table_sql)
conn.commit()

print("✅ 交易日快取表已建立")

# 檢查表結構
cursor.execute("PRAGMA table_info(trading_day_cache)")
columns = cursor.fetchall()
print("\n表結構:")
for col in columns:
    print(f"  - {col[1]} ({col[2]})")

conn.close()
