"""
注意條款資料查詢工具

使用此工具查詢已下載的注意條款資料
"""

import sqlite3
from datetime import datetime, timedelta

def query_attention_clauses(start_date=None, end_date=None, code=None):
    """
    查詢注意條款
    
    Args:
        start_date: 開始日期 (YYYY-MM-DD)，預設為7天前
        end_date: 結束日期 (YYYY-MM-DD)，預設為今天
        code: 股票代碼，不指定則顯示全部
    """
    conn = sqlite3.connect("data/disposal_history.db")
    cursor = conn.cursor()
    
    # 預設日期範圍
    if not end_date:
        end_date = datetime.now().strftime('%Y-%m-%d')
    if not start_date:
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    
    # 查詢
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
    
    results = cursor.fetchall()
    conn.close()
    
    return results

def show_statistics():
    """顯示資料庫統計"""
    conn = sqlite3.connect("data/disposal_history.db")
    cursor = conn.cursor()
    
    # 總筆數
    cursor.execute("SELECT COUNT(*) FROM attention_clauses")
    total = cursor.fetchone()[0]
    
    # 日期範圍
    cursor.execute("SELECT MIN(announce_date), MAX(announce_date) FROM attention_clauses")
    date_range = cursor.fetchone()
    
    # 條款分佈
    cursor.execute("""
        SELECT clause_number, COUNT(*) 
        FROM attention_clauses 
        GROUP BY clause_number 
        ORDER BY clause_number
    """)
    clause_dist = cursor.fetchall()
    
    # 股票數量
    cursor.execute("SELECT COUNT(DISTINCT code) FROM attention_clauses")
    stock_count = cursor.fetchone()[0]
    
    conn.close()
    
    print("="*60)
    print("注意條款資料庫統計")
    print("="*60)
    print(f"總筆數: {total}")
    print(f"日期範圍: {date_range[0]} ~ {date_range[1]}")
    print(f"股票數量: {stock_count}")
    print(f"\n條款分佈:")
    for clause, count in clause_dist:
        print(f"  第 {clause} 款: {count} 筆")
    print("="*60)

def export_to_csv(filename="attention_clauses_export.csv", start_date=None, end_date=None):
    """匯出到 CSV"""
    import csv
    
    results = query_attention_clauses(start_date, end_date)
    
    with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['公告日期', '股票代碼', '股票名稱', '來源', '條款編號', '原因'])
        writer.writerows(results)
    
    print(f"✓ 已匯出 {len(results)} 筆資料到 {filename}")

if __name__ == "__main__":
    import sys
    
    print("注意條款查詢工具")
    print("="*60)
    
    # 顯示統計
    show_statistics()
    
    print("\n查詢最近 7 天的資料：")
    print("-"*60)
    
    results = query_attention_clauses()
    
    if results:
        print(f"找到 {len(results)} 筆資料：\n")
        for row in results[:20]:  # 只顯示前20筆
            announce_date, code, name, source, clause_num, reason = row
            print(f"{announce_date} | {code} {name:8s} | 第{clause_num}款 | {source}")
        
        if len(results) > 20:
            print(f"\n... 還有 {len(results) - 20} 筆（省略顯示）")
    else:
        print("沒有找到資料。請先使用「下載注意條款」功能。")
    
    print("\n" + "="*60)
    print("其他功能：")
    print("  - 匯出 CSV: uv run python query_attention_clauses.py export")
    print("  - 查詢特定股票: 修改程式碼中的 query_attention_clauses(code='2330')")
    print("="*60)
    
    # 匯出功能
    if len(sys.argv) > 1 and sys.argv[1] == 'export':
        export_to_csv()
