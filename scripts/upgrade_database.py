"""
資料庫升級腳本：新增 attention_clauses 資料表

這個腳本會在現有的 disposal_history.db 中新增注意條款資料表
"""

import sqlite3
import os

def upgrade_database(db_path="data/disposal_history.db"):
    """升級資料庫，新增 attention_clauses 資料表"""
    
    if not os.path.exists(db_path):
        print(f"資料庫不存在: {db_path}")
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 檢查資料表是否已存在
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='attention_clauses'
        """)
        
        if cursor.fetchone():
            print("✓ attention_clauses 資料表已存在")
            conn.close()
            return True
        
        # 建立注意條款表
        print("正在建立 attention_clauses 資料表...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS attention_clauses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                announce_date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                source TEXT NOT NULL,
                clause_number INTEGER,
                clause_desc TEXT,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(announce_date, code, clause_number)
            )
        """)
        
        # 建立索引
        print("正在建立索引...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_attention_date ON attention_clauses(announce_date)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_attention_code ON attention_clauses(code)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_attention_date_code ON attention_clauses(announce_date, code)
        """)
        
        conn.commit()
        conn.close()
        
        print("✓ 資料庫升級成功！")
        print("✓ attention_clauses 資料表已建立")
        print("✓ 索引已建立")
        
        return True
        
    except Exception as e:
        print(f"✗ 資料庫升級失敗: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return False


if __name__ == "__main__":
    print("="*60)
    print("資料庫升級腳本 - 新增 attention_clauses 資料表")
    print("="*60)
    
    success = upgrade_database()
    
    if success:
        print("\\n升級完成！可以開始使用注意條款功能。")
    else:
        print("\\n升級失敗，請檢查錯誤訊息。")
