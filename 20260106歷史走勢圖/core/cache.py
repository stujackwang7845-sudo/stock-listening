
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
        
        # Table: agg_cache (Full Table Data)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agg_cache (
                date_str TEXT PRIMARY KEY,
                agg_json TEXT,
                updated_at TEXT
            )
        """)
        
        # Table: chart_cache (Minutely K-Lines)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chart_cache (
                cache_key TEXT PRIMARY KEY, 
                chart_json TEXT,
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

    def get_agg_data(self, date_str):
        """Returns full agg_data dict or None."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT agg_json FROM agg_cache WHERE date_str = ?", (date_str,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return json.loads(row[0])
        except Exception as e:
            print(f"AggRead Error: {e}")
        return None

    def save_agg_data(self, date_str, agg_data):
        """Saves agg_data dict."""
        if not agg_data: return
        try:
            json_str = json.dumps(agg_data, ensure_ascii=False)
            now = datetime.now().isoformat()
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO agg_cache (date_str, agg_json, updated_at)
                VALUES (?, ?, ?)
            """, (date_str, json_str, now))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"AggWrite Error: {e}")

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

    def get_chart_data(self, stock_id, date_str):
        """Returns JSON string/obj of chart data or None."""
        cache_key = f"{stock_id}_{date_str}"
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT chart_json FROM chart_cache WHERE cache_key = ?", (cache_key,))
            row = cursor.fetchone()
            conn.close()
            if row:
                 return row[0] # Return raw json string to be parsed by pandas
        except Exception as e:
            print(f"Chart Read Error ({cache_key}): {e}")
        return None

    def save_chart_data(self, stock_id, date_str, json_str):
        """Saves chart data (expects JSON string)."""
        if not json_str: return
        cache_key = f"{stock_id}_{date_str}"
        try:
            now = datetime.now().isoformat()
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO chart_cache (cache_key, chart_json, updated_at)
                VALUES (?, ?, ?)
            """, (cache_key, json_str, now))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Chart Write Error ({cache_key}): {e}")
