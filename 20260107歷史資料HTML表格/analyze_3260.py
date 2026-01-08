"""
分析 3260 為何沒有顯示的腳本
"""
from datetime import datetime

# 從截圖看到的 DEBUG 訊息
debug_data = [
    ("1528", "連續3個營業日及沖銷標準", "2026-01-02 00:00:00", "2026-01-15 00:00:00"),
    ("3354", "連續3個營業日及沖銷標準", "2025-12-31 00:00:00"),
]

# 3260 的資料 (從 API 測試得知)
# Period: 連續3個營業日及沖銷標準
# 但沒有具體日期?

print("=" * 60)
print("分析: 為什麼 3260 沒有在 DEBUG 訊息中出現?")
print("=" * 60)

print("\n條件檢查:")
print("1. data.get('is_disposed', False) - 必須為 True")
print("2. disp_start_dt - 必須存在")
print("3. disp_start_dt > today_dt - 生效日必須在未來")

print("\n從 DEBUG 訊息看到:")
print("- 4304, 5351, 8299, 82992 都有進入過濾邏輯")
print("- 3260 沒有出現")

print("\n可能原因:")
print("1. 3260 的 period 沒有包含日期,導致 disp_start_dt 為 None")
print("2. 3260 的生效日不是未來(已經生效)")
print("3. 3260 沒有被標記為 is_disposed")

print("\n建議:")
print("在 dashboard.py 第 810 行加入 DEBUG,檢查所有被標記為 is_disposed 的上櫃股票")
print("=" * 60)
