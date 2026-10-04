"""
清除並重新下載注意條款資料
"""

import sqlite3
from datetime import datetime, timedelta
from core.utils import DateUtils
from core.scraper_attention import AttentionScraper
from core.clause_parser import ClauseNumberParser
from core import database_clause_ext

print("="*60)
print("清除並重新下載注意條款資料")
print("="*60)

# 1. 刪除舊資料
conn = sqlite3.connect("data/disposal_history.db")
cursor = conn.cursor()

cursor.execute("SELECT COUNT(*) FROM attention_clauses")
old_count = cursor.fetchone()[0]
print(f"\n舊資料筆數: {old_count}")

cursor.execute("DELETE FROM attention_clauses")
conn.commit()
print("✓ 已清除舊資料")

# 2. 計算日期範圍（2026-01-20 到 2026-01-30）
start_date = datetime(2026, 1, 20)
end_date = datetime(2026, 1, 30)

dates = []
current = start_date
while current <= end_date:
    if DateUtils.is_trading_day(current):
        dates.append(current)
    current += timedelta(days=1)

print(f"\n將下載 {len(dates)} 個交易日的資料")
print(f"日期範圍: {dates[0].strftime('%Y/%m/%d')} ~ {dates[-1].strftime('%Y/%m/%d')}")

# 3. 下載並儲存
all_records = []
total_downloaded = 0

for idx, date in enumerate(dates, 1):
    date_str = date.strftime('%Y/%m/%d')
    print(f"\n[{idx}/{len(dates)}] 下載 {date_str}...", end=" ")
    
    data = AttentionScraper.fetch_data(date)
    
    if not data:
        print("無資料")
        continue
    
    print(f"原始: {len(data)} 筆", end=" ")
    total_downloaded += len(data)
    
    # 處理每筆資料
    filtered = 0
    for item in data:
        code = item.get('code', '').strip()
        
        # 只保留 4 碼股票
        if not code or len(code) != 4:
            continue
        
        reason = item.get('reason', '')
        clauses = ClauseNumberParser.parse(reason)
        
        # 只儲存有條款的資料（1-8款）
        if not clauses:
            continue
        
        for clause_num in clauses:
            all_records.append({
                'announce_date': date.strftime('%Y-%m-%d'),
                'code': code,
                'name': item.get('name', ''),
                'source': item.get('source', 'TWSE'),
                'clause_number': clause_num,
                'reason': reason
            })
        filtered += 1
    
    print(f"→ 儲存: {filtered} 筆")

# 4. 批量儲存
if all_records:
    saved_count = database_clause_ext.save_attention_clauses(conn, all_records)
    print(f"\n{'='*60}")
    print(f"✓ 成功儲存 {saved_count} 筆資料")
    
    # 5. 統計
    cursor.execute("SELECT DISTINCT source FROM attention_clauses")
    sources = cursor.fetchall()
    print(f"\n資料來源: {[s[0] for s in sources]}")
    
    cursor.execute("""
        SELECT source, COUNT(*) 
        FROM attention_clauses 
        GROUP BY source
    """)
    for source, count in cursor.fetchall():
        print(f"  {source}: {count} 筆")
    
    cursor.execute("SELECT COUNT(DISTINCT code) FROM attention_clauses")
    stock_count = cursor.fetchone()[0]
    print(f"\n股票數量: {stock_count}")
    
    print(f"\n總下載: {total_downloaded} 筆（原始資料）")
    print(f"總儲存: {saved_count} 筆（過濾後：4碼股票 + 1-8款）")
else:
    print("\n⚠️ 沒有找到符合條件的資料")

conn.close()
print("="*60)
