"""
DisposalDatabase 擴充方法

這個檔案包含注意條款相關的資料庫操作方法
由於直接修改 disposal_database.py 有困難，這裡提供擴充方法
"""

from typing import List, Dict


def save_attention_clauses(conn, records: List[Dict]) -> int:
    """
    批量儲存注意條款
    
    Args:
        conn: sqlite3 connection
        records: 條款記錄列表，每筆包含:
            - announce_date: 公告日期 (YYYY-MM-DD)
            - code: 股票代碼
            - name: 股票名稱
            - source: 來源 ('TWSE' 或 'TPEX')
            - clause_number: 條款編號 (1-8)
            - reason: 原始 reason 文字
    
    Returns:
        成功儲存的筆數
    """
    if not records:
        return 0
        
    try:
        cursor = conn.cursor()
        saved_count = 0
        
        for record in records:
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO attention_clauses
                    (announce_date, code, name, source, clause_number, reason)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    record['announce_date'],
                    record['code'],
                    record['name'],
                    record['source'],
                    record['clause_number'],
                    record.get('reason', '')
                ))
                saved_count += 1
            except Exception as e:
                print(f"儲存條款失敗 ({record.get('code', 'Unknown')}): {e}")
        
        conn.commit()
        print(f"✓ 已儲存 {saved_count} 筆注意條款")
        return saved_count
        
    except Exception as e:
        print(f"批量儲存注意條款失敗: {e}")
        conn.rollback()
        return 0


def get_attention_clauses(conn, start_date: str, end_date: str, code: str = None) -> List[Dict]:
    """
    查詢指定日期範圍的注意條款
    
    Args:
        conn: sqlite3 connection
        start_date: 開始日期 (YYYY-MM-DD)
        end_date: 結束日期 (YYYY-MM-DD)
        code: 可選，指定股票代碼
    
    Returns:
        條款記錄列表
    """
    cursor = conn.cursor()
    
    if code:
        cursor.execute("""
            SELECT * FROM attention_clauses
            WHERE announce_date BETWEEN ? AND ? AND code = ?
            ORDER BY announce_date DESC, code, clause_number
        """, (start_date, end_date, code))
    else:
        cursor.execute("""
            SELECT * FROM attention_clauses
            WHERE announce_date BETWEEN ? AND ?
            ORDER BY announce_date DESC, code, clause_number
        """, (start_date, end_date))
    
    columns = [desc[0] for desc in cursor.description]
    results = []
    for row in cursor.fetchall():
        results.append(dict(zip(columns, row)))
    
    return results


def delete_attention_clauses_by_date(conn, date: str) -> int:
    """
    刪除指定日期的所有注意條款（用於重新下載）
    
    Args:
        conn: sqlite3 connection
        date: 日期 (YYYY-MM-DD)
    
    Returns:
        刪除的筆數
    """
    try:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM attention_clauses WHERE announce_date = ?
        """, (date,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        
        if deleted_count > 0:
            print(f"已刪除 {date} 的 {deleted_count} 筆注意條款")
        
        return deleted_count
        
    except Exception as e:
        print(f"刪除注意條款失敗: {e}")
        conn.rollback()
        return 0


def get_clauses_for_date(conn, announce_date):
    """
    查詢指定日期的所有注意條款（合併格式）
    
    Args:
        conn: 資料庫連線
        announce_date: 日期字串 'YYYY-MM-DD' 或 'MM/DD'
    
    Returns:
        dict: {code: '一,四', ...}
        例如: {'2330': '一,四', '3008': '二'}
    """
    from collections import defaultdict
    cursor = conn.cursor()
    
    # 如果是 MM/DD 格式，轉換為 YYYY-MM-DD
    if len(announce_date) <= 5 and '/' in announce_date:
        # 假設是當年或最近的年份
        from datetime import datetime
        current_year = datetime.now().year
        
        try:
            month_day = announce_date.split('/')
            month = int(month_day[0])
            day = int(month_day[1])
            announce_date = f"{current_year}-{month:02d}-{day:02d}"
        except:
            return {}
    
    try:
        cursor.execute("""
            SELECT code, name, source, clause_number 
            FROM attention_clauses 
            WHERE announce_date = ?
            ORDER BY code, clause_number
        """, (announce_date,))
        
        results = cursor.fetchall()

        # [Fix 2026-09-07 使用者要求] 儀表板下方表格的條款摘要只要顯示 1~8 款——這四條
        # 核心觸發規則(連續3天第一款/連續5天任一款/10日6次/30日12次)本來就只看第1~8款
        # (詳見 predictor.py 的 is_any 定義)，第0款(注意但無具體條款)、第9~14款(全額交割/
        # 管理股票/當沖占比過高等其他性質的公告)混在同一個摘要字串裡只會造成雜訊，跟這張
        # 表格要呈現的「四條規則累積進度」無關。這裡只過濾「顯示用的字串」，不動
        # attention_clauses 資料表本身——原始的0/9~14款紀錄仍然完整保留，供其他用途
        # (例如判斷處置5天/7天要用到的第十三款)查詢。
        num_to_chinese = {
            1: '一', 2: '二', 3: '三', 4: '四',
            5: '五', 6: '六', 7: '七', 8: '八',
        }

        # 合併同一股票的條款
        clauses_map = defaultdict(list)
        info_map = {}
        for code, name, source, clause_num in results:
            if clause_num not in num_to_chinese:
                continue
            chinese_num = num_to_chinese[clause_num]

            if str(code) not in info_map:
                info_map[str(code)] = {"name": name or "", "source": source or "上市"}

            clauses_map[str(code)].append(chinese_num)
        
        # 轉換為帶有基本資訊的字典
        final_map = {}
        for code, clauses in clauses_map.items():
            final_map[code] = {
                "clauses": ','.join(clauses),
                "name": info_map[code]["name"],
                "source": info_map[code]["source"]
            }
        
        return final_map
        
    except Exception as e:
        print(f"[Database] Error querying clauses for {announce_date}: {e}")
        return {}
