
import sqlite3
import json
import os
from datetime import datetime

class CacheManager:
    def __init__(self, db_path="data/cache.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        # Ensure directory exists
        folder = os.path.dirname(self.db_path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder)
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # Table: daily_cache
        # date_str: YYYYMMDD
        # data_json: List of items (dicts)
        # updated_at: Timestamp
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_cache (
                date_str TEXT PRIMARY KEY,
                data_json TEXT,
                updated_at TEXT
            )
        """)
        
        # Table: dashboard_summary (For top 3 blocks: Observer, Notice, Exit)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_summary (
                date_str TEXT PRIMARY KEY,
                summary_json TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def get_daily_data(self, date_str):
        """Returns list of items or None if not found."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT data_json FROM daily_cache WHERE date_str = ?", (date_str,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return json.loads(row[0])
        except Exception as e:
            print(f"Cache Read Error ({date_str}): {e}")
            
        return None

    def save_daily_data(self, date_str, data_list):
        """Saves list of items to cache."""
        if data_list is None:
            return
            
        try:
            json_str = json.dumps(data_list, ensure_ascii=False)
            now = datetime.now().isoformat()
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO daily_cache (date_str, data_json, updated_at)
                VALUES (?, ?, ?)
            """, (date_str, json_str, now))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Cache Write Error ({date_str}): {e}")

    def get_dashboard_summary(self, date_str):
        """Returns dict of {obs, notice, exit} or None."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT summary_json FROM dashboard_summary WHERE date_str = ?", (date_str,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return json.loads(row[0])
        except Exception as e:
            print(f"Summary Read Error ({date_str}): {e}")
        return None

    def save_dashboard_summary(self, date_str, summary_data):
        """Saves dict of {obs, notice, exit}."""
        if not summary_data:
            return
            
        try:
            json_str = json.dumps(summary_data, ensure_ascii=False)
            now = datetime.now().isoformat()
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO dashboard_summary (date_str, summary_json, updated_at)
                VALUES (?, ?, ?)
            """, (date_str, json_str, now))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Summary Write Error ({date_str}): {e}")
