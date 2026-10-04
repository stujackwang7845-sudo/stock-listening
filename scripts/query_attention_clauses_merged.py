"""
注意條款資料查詢工具（官方格式版本）

將同一股票同一天的多個條款合併顯示
"""

import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

def query_attention_clauses_merged(start_date=None, end_date=None, code=None):
    """
    查詢注意條款（合併格式）
    
    將同一股票同一天的多個條款合併成一筆
    """
    conn = sqlite3.connect("data/disposal_history.db")
    cursor = conn.cursor()
    
    # 預設日期範圍
    if not end_date:
        end_date = datetime.now().strftime('%Y-%m-%d')
    if not start_date:
        start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
    
    # 查詢所有資料
    if code:
        cursor.execute("""
            SELECT announce_date, code, name, source, clause_number, reason
            FROM attention_clauses
            WHERE announce_date BETWEEN ? AND ? AND code = ?
            ORDER BY announce_date DESC, code, clause_number
        """, (start_date, end_date, code))
    else:
        cursor.execute("""
            SELECT announce_date, code, name, source, clause_number, reason
            FROM attention_clauses
            WHERE announce_date BETWEEN ? AND ?
            ORDER BY announce_date DESC, code, clause_number
        """, (start_date, end_date))
    
    raw_results = cursor.fetchall()
    conn.close()
    
    # 合併同一天同一股票的條款
    merged = defaultdict(lambda: {
        'announce_date': '',
        'code': '',
        'name': '',
        'source': '',
        'clauses': [],
        'reason': ''
    })
    
    for announce_date, code, name, source, clause_num, reason in raw_results:
        key = f"{announce_date}_{code}"
        
        if not merged[key]['code']:
            merged[key]['announce_date'] = announce_date
            merged[key]['code'] = code
            merged[key]['name'] = name
            merged[key]['source'] = source
            merged[key]['reason'] = reason
        
        merged[key]['clauses'].append(clause_num)
    
    # 轉換成列表
    result = []
    for data in merged.values():
        clauses_str = '、'.join([f'第{c}款' for c in sorted(data['clauses'])])
        
        # 轉換 source 為中文顯示
        source_display = data['source']
        if source_display == 'TWSE':
            source_display = '上市'
        elif source_display == 'TPEX':
            source_display = '上櫃'
        
        result.append((
            data['announce_date'],
            data['code'],
            data['name'],
            source_display,
            clauses_str,
            data['reason']
        ))
    
    return result

def show_statistics_merged():
    """顯示統計（合併格式）"""
    conn = sqlite3.connect("data/disposal_history.db")
    cursor = conn.cursor()
    
    # 計算合併後的筆數
    cursor.execute("""
        SELECT announce_date, code, COUNT(DISTINCT clause_number) as clause_count
        FROM attention_clauses
        GROUP BY announce_date, code
    """)
    
    grouped = cursor.fetchall()
    total_merged = len(grouped)
    
    # 原始筆數
    cursor.execute("SELECT COUNT(*) FROM attention_clauses")
    total_raw = cursor.fetchone()[0]
    
    # 日期範圍
    cursor.execute("SELECT MIN(announce_date), MAX(announce_date) FROM attention_clauses")
    date_range = cursor.fetchone()
    
    # 股票數量
    cursor.execute("SELECT COUNT(DISTINCT code) FROM attention_clauses")
    stock_count = cursor.fetchone()[0]
    
    # 各日期的合併筆數
    cursor.execute("""
        SELECT announce_date, COUNT(DISTINCT code)
        FROM attention_clauses
        GROUP BY announce_date
        ORDER BY announce_date DESC
    """)
    date_counts = cursor.fetchall()
    
    conn.close()
    
    print("="*60)
    print("注意條款資料庫統計（官方格式）")
    print("="*60)
    print(f"合併後總筆數: {total_merged} 筆")
    print(f"原始總筆數: {total_raw} 筆（拆開後）")
    print(f"日期範圍: {date_range[0]} ~ {date_range[1]}")
    print(f"股票數量: {stock_count}")
    print(f"\n各日期公告筆數:")
    for date, count in date_counts:
        print(f"  {date}: {count} 筆")
    print("="*60)

def export_to_csv_merged(filename="attention_clauses_merged.csv", start_date=None, end_date=None):
    """匯出到 CSV（官方格式）"""
    import csv
    
    results = query_attention_clauses_merged(start_date, end_date)
    
    with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['公告日期', '股票代碼', '股票名稱', '來源', '條款', '原因'])
        writer.writerows(results)
    
    print(f"✓ 已匯出 {len(results)} 筆資料到 {filename}")

if __name__ == "__main__":
    import sys
    
    print("注意條款查詢工具（官方格式）")
    print("="*60)
    
    # 顯示統計
    show_statistics_merged()
    
    print("\n查詢最近資料（合併格式）：")
    print("-"*60)
    
    # 使用完整日期範圍
    results = query_attention_clauses_merged(start_date='2026-01-20', end_date='2026-01-30')
    
    if results:
        print(f"找到 {len(results)} 筆資料（合併後）：\n")
        for row in results[:30]:  # 顯示前30筆
            announce_date, code, name, source, clauses, reason = row
            print(f"{announce_date} | {code} {name:10s} | {clauses:20s} | {source}")
        
        if len(results) > 30:
            print(f"\n... 還有 {len(results) - 30} 筆（省略顯示）")
    else:
        print("沒有找到資料。")
    
    print("\n" + "="*60)
    print("匯出功能：")
    print("  uv run python query_attention_clauses_merged.py export")
    print("="*60)
    
    # 匯出功能
    if len(sys.argv) > 1 and sys.argv[1] == 'export':
        export_to_csv_merged(start_date='2026-01-20', end_date='2026-01-30')
