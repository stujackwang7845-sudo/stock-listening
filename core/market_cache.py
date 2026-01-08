import sqlite3
import pandas as pd
import os
from datetime import datetime

class MarketDataCache:
    def __init__(self, db_path="data/market_data.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        folder = os.path.dirname(self.db_path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder)
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Table: price_history
        # Composite PK: stock_id + date
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                stock_id TEXT,
                date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY (stock_id, date)
            )
        """)
        
        # Table: stock_info
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_info (
                stock_id TEXT PRIMARY KEY,
                shares_outstanding REAL,
                updated_at TEXT
            )
        """)
        
        # Index for faster query
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_date ON price_history (date)")
        
        # Table: ratios (PER, PBR)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ratios (
                stock_id TEXT,
                date TEXT,
                per REAL,
                pbr REAL,
                PRIMARY KEY (stock_id, date)
            )
        """)
        
        conn.commit()
        conn.close()

    def get_price_history(self, stock_id, start_date=None):
        """Returns DataFrame with OHLCV or None."""
        try:
            conn = sqlite3.connect(self.db_path)
            query = "SELECT date, open, high, low, close, volume FROM price_history WHERE stock_id = ?"
            params = [stock_id]
            
            if start_date:
                query += " AND date >= ?"
                params.append(start_date)
            
            query += " ORDER BY date ASC"
            
            df = pd.read_sql(query, conn, params=params)
            conn.close()
            
            if df.empty:
                return None
                
            # Convert date to datetime
            df['date'] = pd.to_datetime(df['date'])
            # Rename cols to match standard (Capitalized)
            df = df.rename(columns={
                'date': 'Date', 'open': 'Open', 'high': 'High', 
                'low': 'Low', 'close': 'Close', 'volume': 'Volume'
            })
            df.set_index('Date', inplace=True)
            return df
        except Exception as e:
            print(f"Cache Read Error: {e}")
            return None

    def save_price_history(self, stock_id, df):
        """Saves OHLCV DataFrame to DB."""
        if df is None or df.empty:
            return

        try:
            conn = sqlite3.connect(self.db_path)
            # Prepare data
            # Expects df index to be Date (or valid column)
            data_to_insert = []
            
            # Reset index if Date is index
            df_reset = df.reset_index()
            
            for _, row in df_reset.iterrows():
                # Flexible column names
                d = row.get('Date') or row.get('date')
                o = row.get('Open') or row.get('open')
                h = row.get('High') or row.get('high')
                l = row.get('Low') or row.get('low')
                c = row.get('Close') or row.get('close')
                v = row.get('Volume') or row.get('volume')
                
                if pd.isna(d): continue
                
                # Format date string YYYY-MM-DD
                if isinstance(d, (pd.Timestamp, datetime)):
                    d_str = d.strftime('%Y-%m-%d')
                else:
                    d_str = str(d).split(' ')[0] # Handle strings
                
                data_to_insert.append((stock_id, d_str, o, h, l, c, v))
                
            cursor = conn.cursor()
            cursor.executemany("""
                INSERT OR REPLACE INTO price_history (stock_id, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, data_to_insert)
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Cache Save Error: {e}")

    def get_ratios(self, stock_id, start_date=None):
        """Returns DataFrame with PER, PBR or None."""
        try:
            conn = sqlite3.connect(self.db_path)
            query = "SELECT date, per, pbr FROM ratios WHERE stock_id = ?"
            params = [stock_id]
            if start_date:
                query += " AND date >= ?"
                params.append(start_date)
            query += " ORDER BY date ASC"
            
            df = pd.read_sql(query, conn, params=params)
            conn.close()
            
            if df.empty: return None
            
            df['date'] = pd.to_datetime(df['date'])
            df = df.rename(columns={'date': 'Date', 'per': 'PER', 'pbr': 'PBR'})
            df.set_index('Date', inplace=True)
            return df
        except Exception:
            return None

    def save_ratios(self, stock_id, df):
        """Saves PER/PBR DataFrame to DB."""
        if df is None or df.empty: return
        try:
            conn = sqlite3.connect(self.db_path)
            data_to_insert = []
            df_reset = df.reset_index()
            
            for _, row in df_reset.iterrows():
                d = row.get('Date') or row.get('date')
                per = row.get('PER') or row.get('benefit_ratio')
                pbr = row.get('PBR') or row.get('pb_ratio')
                
                if pd.isna(d): continue
                if isinstance(d, (pd.Timestamp, datetime)):
                    d_str = d.strftime('%Y-%m-%d')
                else:
                    d_str = str(d).split(' ')[0]
                    
                data_to_insert.append((stock_id, d_str, per, pbr))
                
            cursor = conn.cursor()
            cursor.executemany("""
                INSERT OR REPLACE INTO ratios (stock_id, date, per, pbr)
                VALUES (?, ?, ?, ?)
            """, data_to_insert)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Ratios Save Error: {e}")

    def get_stock_info(self, stock_id):
        """Returns shares_outstanding or None."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT shares_outstanding FROM stock_info WHERE stock_id = ?", (stock_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return row[0]
            return None
        except Exception:
            return None

    def save_stock_info(self, stock_id, shares):
        try:
            conn = sqlite3.connect(self.db_path)
            updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO stock_info (stock_id, shares_outstanding, updated_at)
                VALUES (?, ?, ?)
            """, (stock_id, shares, updated))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Info Save Error: {e}")
