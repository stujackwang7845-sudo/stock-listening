"""
處置資料庫管理系統

提供處置資料的匯入、查詢和更新功能
"""

import sqlite3
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional, Tuple


class DisposalDatabase:
    """處置資料庫管理類別"""

    def __init__(self, db_path=None):
        """
        初始化資料庫
        
        Args:
            db_path: 資料庫檔案路徑
        """
        from core.runtime import get_paths
        self.db_path = db_path or get_paths().disposal_db
        self.conn = None
        self._init_database()
    
    def _init_database(self):
        """初始化資料庫結構"""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()
        
        # 建立處置紀錄表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS disposal_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,  -- '上市' 或 '上櫃'
                announce_date TEXT,  -- 公布日期
                code TEXT NOT NULL,  -- 證券代號
                name TEXT,  -- 證券名稱
                period_start TEXT,  -- 處置開始日
                period_end TEXT,  -- 處置結束日
                period_raw TEXT,  -- 原始處置起迄時間字串
                measure TEXT,  -- 處置措施
                reason TEXT,  -- 處置原因
                custom_stats TEXT, -- 手動修改的漲跌幅 (JSON)
                calculated_stats TEXT, -- [New] 自動計算的漲跌幅緩存 (JSON)
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, code, announce_date, period_raw)
            )
        """)
        
        # 檢查是否需要新增 custom_stats 欄位 (遷移舊資料庫)
        try:
            cursor.execute("SELECT custom_stats FROM disposal_records LIMIT 0")
        except sqlite3.OperationalError:
            print("Adding custom_stats column to disposal_records table...")
            cursor.execute("ALTER TABLE disposal_records ADD COLUMN custom_stats TEXT")

        # [New] 檢查是否需要新增 calculated_stats 欄位
        try:
            cursor.execute("SELECT calculated_stats FROM disposal_records LIMIT 0")
        except sqlite3.OperationalError:
            print("Adding calculated_stats column to disposal_records table...")
            cursor.execute("ALTER TABLE disposal_records ADD COLUMN calculated_stats TEXT")
        
        # 建立索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_code ON disposal_records(code)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_announce_date ON disposal_records(announce_date)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_period ON disposal_records(period_start, period_end)
        """)
        
        self.conn.commit()
        print(f"資料庫已初始化: {self.db_path}")
    
    def parse_period(self, period_str: str) -> Tuple[Optional[str], Optional[str]]:
        """
        解析處置起迄時間（民國年格式）
        
        Args:
            period_str: 例如 "114/12/23~115/01/09"
        
        Returns:
            (start_date, end_date) ISO 格式 "YYYY-MM-DD" 或 (None, None)
        """
        if not period_str or pd.isna(period_str):
            return None, None
        
        try:
            parts = []
            period_str_clean = str(period_str).strip().replace("～", "~")
            if "~" in period_str_clean:
                parts = period_str_clean.split("~")
            elif "-" in period_str_clean:
                 # Handle simple range "1150113-1150126"
                 if period_str_clean.count("-") == 1:
                     parts = period_str_clean.split("-")
            
            if len(parts) != 2:
                # Last resort: if 14 digits? No, simple return None
                return None, None
            
            def convert_roc_to_iso(roc_str):
                """民國年轉 ISO 日期"""
                roc_str = roc_str.strip()
                if not roc_str or roc_str == "-":
                    return None
                
                # Handle formats
                # 1. 115/01/22
                if "/" in roc_str:
                    date_parts = roc_str.split("/")
                    if len(date_parts) == 3:
                        roc_year = int(date_parts[0])
                        month = int(date_parts[1])
                        day = int(date_parts[2])
                        return f"{roc_year + 1911:04d}-{month:02d}-{day:02d}"
                
                # 2. 1150122 (7 digit ROC)
                if len(roc_str) == 7 and roc_str.isdigit():
                    roc_year = int(roc_str[:3])
                    month = int(roc_str[3:5])
                    day = int(roc_str[5:])
                    return f"{roc_year + 1911:04d}-{month:02d}-{day:02d}"

                return None
            
            start_date = convert_roc_to_iso(parts[0])
            end_date = convert_roc_to_iso(parts[1])
            
            return start_date, end_date
        
        except Exception as e:
            print(f"解析日期失敗: {period_str} - {e}")
            return None, None
    
    def import_from_csv(self, csv_path: str, source: str, skip_rows: int = 2) -> int:
        """
        從 CSV 匯入處置資料
        
        Args:
            csv_path: CSV 檔案路徑
            source: 資料來源 ('上市' 或 '上櫃')
            skip_rows: 跳過前幾行
        
        Returns:
            成功匯入的筆數
        """
        print(f"\n開始匯入 {source} 處置資料...")
        print(f"檔案: {csv_path}")
        
        try:
            # 讀取 CSV (強制字串型態，避免代號變成 float 如 4931.0)
            df = pd.read_csv(csv_path, encoding='cp950', skiprows=skip_rows, dtype=str)
            
            # 找出欄位名稱（處理可能的空白或不同命名）
            col_map = {}
            for col in df.columns:
                col_clean = str(col).strip().replace('\u3000', '') # Remove full-width space
                if '公布日期' in col_clean or '日期' in col_clean:
                    if '起' not in col_clean and '迄' not in col_clean: # Avoid 處置起迄
                        col_map['announce_date'] = col
                elif '證券代號' in col_clean or '代號' in col_clean:
                    col_map['code'] = col
                elif '證券名稱' in col_clean or '名稱' in col_clean:
                    col_map['name'] = col
                elif '處置起' in col_clean or '期間' in col_clean:  # 匹配「處置起訖時間」或「處置起迄」
                    col_map['period'] = col
                elif '處置措施' in col_clean or '措施' in col_clean:
                    col_map['measure'] = col
                elif '處置原因' in col_clean or '原因' in col_clean:
                    col_map['reason'] = col
                elif '處置內容' in col_clean or '內容' in col_clean:
                    col_map['content'] = col
                elif '處置條件' in col_clean or '條件' in col_clean:
                    col_map['condition'] = col
            
            print(f"偵測到欄位: {col_map}")
            
            # Fallback for known headers if map is empty (e.g. garbled headers)
            if 'announce_date' not in col_map and len(df.columns) >= 6:
                print("Warning: Auto-detect failed, using integer index mapping (Risky)")
                # Assume standard format: Index, Date, Code, Name, Period, Measure, Reason
                # publish2.csv: 0=Index, 1=Date, 2=Code, 3=Name, 4=Period, 5=Measure, 6=Reason
                col_names = df.columns.tolist()
                col_map['announce_date'] = col_names[1]
                col_map['code'] = col_names[2]
                col_map['name'] = col_names[3]
                col_map['period'] = col_names[4]
                col_map['measure'] = col_names[5]
                col_map['reason'] = col_names[6] if len(col_names) > 6 else None
            
            # 準備匯入
            cursor = self.conn.cursor()
            imported_count = 0
            skipped_count = 0
            
            for idx, row in df.iterrows():
                try:
                    # 提取資料
                    announce_date = row.get(col_map.get('announce_date', ''), None)
                    code = row.get(col_map.get('code', ''), None)
                    name = row.get(col_map.get('name', ''), None)
                    period_raw = row.get(col_map.get('period', ''), None)
                    
                    measure_text = row.get(col_map.get('measure', ''), '')
                    reason_text = row.get(col_map.get('reason', ''), '')
                    content_text = row.get(col_map.get('content', ''), '')
                    condition_text = row.get(col_map.get('condition', ''), '')
                    
                    measure = str(measure_text) if not pd.isna(measure_text) else ""
                    if str(content_text).strip() and not pd.isna(content_text):
                        measure += f" {content_text}"
                    
                    reason = str(reason_text) if not pd.isna(reason_text) else ""
                    if str(condition_text).strip() and not pd.isna(condition_text):
                        reason += f" {condition_text}"
                    
                    # 跳過無效資料
                    if pd.isna(code) or not str(code).strip():
                        skipped_count += 1
                        continue
                    
                    # 清理代號（移除等號和引號）
                    code = str(code).replace('=', '').replace('"', '').replace('.0', '').strip()
                    
                    # 統一將 announce_date 轉為 YYYY-MM-DD
                    if announce_date and not pd.isna(announce_date):
                        from core.utils import DateUtils
                        iso_announce = DateUtils.to_iso_date_str(str(announce_date).strip())
                        final_announce = iso_announce if iso_announce else str(announce_date).strip()
                    else:
                        final_announce = None
                    
                    # 解析處置期間
                    period_start, period_end = self.parse_period(period_raw)
                    
                    # 插入資料庫
                    cursor.execute("""
                        INSERT OR REPLACE INTO disposal_records
                        (source, announce_date, code, name, period_start, period_end, 
                         period_raw, measure, reason, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
                        source,
                        final_announce,
                        code,
                        str(name).strip() if not pd.isna(name) else None,
                        period_start,
                        period_end,
                        str(period_raw).strip() if not pd.isna(period_raw) else None,
                        measure.strip() if measure else None,
                        reason.strip() if reason else None
                    ))
                    
                    imported_count += 1
                    
                    if imported_count % 500 == 0:
                        print(f"  已匯入 {imported_count} 筆...")
                
                except Exception as e:
                    print(f"  警告: 第 {idx} 筆資料匯入失敗 - {e}")
                    skipped_count += 1
            
            self.conn.commit()
            print(f"[OK] {source} 資料匯入完成: 成功 {imported_count} 筆, 跳過 {skipped_count} 筆")
            return imported_count
        
        except Exception as e:
            print(f"[ERROR] 匯入失敗: {e}")
            self.conn.rollback()
            return 0
    
    def get_disposal_by_code(self, code: str) -> List[Dict]:
        """查詢特定股票的所有處置紀錄"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM disposal_records 
            WHERE code = ? 
            ORDER BY announce_date DESC
        """, (code,))
        
        columns = [desc[0] for desc in cursor.description]
        results = []
        for row in cursor.fetchall():
            results.append(dict(zip(columns, row)))
        
        return results
    
    def is_announced_on(self, code: str, target_date: str) -> bool:
        """
        檢查是否在 target_date 公告處置/注意
        target_date: "YYYY-MM-DD"
        DB stores announce_date as "115/01/22" (ROC format) usually.
        """
        cursor = self.conn.cursor()
        
        # Convert AD to ROC
        roc_variants = []
        try:
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            roc_year = dt.year - 1911
            
            # Variant 1: 115/01/22 (Standard padded)
            roc_variants.append(f"{roc_year}/{dt.month:02d}/{dt.day:02d}")
            
            # Variant 2: 115/1/22 (No padding) - sometimes happens
            roc_variants.append(f"{roc_year}/{dt.month}/{dt.day}")
            
            # Variant 3: YYYYMMDD (e.g. 20260122) - found in some CSVs
            roc_variants.append(dt.strftime("%Y%m%d"))
            
            # Variant 4: YYYY/MM/DD - found in records inserted by workers
            roc_variants.append(dt.strftime("%Y/%m/%d"))
            
        except:
            pass
            
        # Build flexible query
        query = f"""
            SELECT 1 FROM disposal_records 
            WHERE code = ? AND (
                announce_date = ? 
                OR announce_date = ? 
                OR announce_date = ?
                OR announce_date = ?
                OR period_start = ?
                OR period_start = ?
            )
            LIMIT 1
        """
        
        args = [code, target_date] # Standard ISO
        args.extend(roc_variants) # Add ROCs + YYYYMMDD
        
        # Ensure we have enough placeholders vs args
        # Current placeholders: 1 (iso) + len(variants) + 1 (period) = ?
        
        # Let's use simpler IN logic ? No, SQLite IN is fine but params need to match
        
        # Dynamic Construction
        all_dates = [target_date] + roc_variants
        # Add period_start check (just ISO target)
        
        # Where (announce_date IN (?, ?, ?, ...)) OR (period_start = ?)
        
        placeholders = ", ".join(["?"] * len(all_dates))
        sql = f"""
            SELECT 1 FROM disposal_records 
            WHERE code = ? AND (
                announce_date IN ({placeholders})
                OR period_start = ?
            )
            LIMIT 1
        """
        
        params = [code] + all_dates + [target_date]
        
        cursor.execute(sql, params)
        
        return cursor.fetchone() is not None

    
    def get_disposal_by_announce_date(self, date_str: str) -> List[Dict]:
        """查詢特定公告日期的所有處置紀錄 (date_str: YYYY-MM-DD or YYYY/MM/DD)"""
        
        # Normalize to all variants
        # We try to construct 3 variants:
        # 1. 2026/01/28
        # 2. 2026-01-28
        # 3. 20260128
        
        # Base cleanup
        clean = date_str.replace("/", "").replace("-", "").replace(".", "")
        if len(clean) == 8:
             v_slash = f"{clean[:4]}/{clean[4:6]}/{clean[6:]}"
             v_dash = f"{clean[:4]}-{clean[4:6]}-{clean[6:]}"
             v_plain = clean
        else:
             # Fallback
             v_slash = date_str.replace("-", "/")
             v_dash = date_str.replace("/", "-")
             v_plain = date_str.replace("/", "").replace("-", "")

        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM disposal_records 
            WHERE announce_date = ? 
               OR announce_date = ? 
               OR announce_date = ?
        """, (v_slash, v_dash, v_plain))
        
        columns = [desc[0] for desc in cursor.description]
        results = []
        for row in cursor.fetchall():
            results.append(dict(zip(columns, row)))
            
        return results
    
    def get_active_disposals(self, ref_date: str = None) -> List[Dict]:
        """
        取得目前進行中的處置
        
        Args:
            ref_date: 參考日期 (ISO格式)，預設為今天
        """
        if ref_date is None:
            ref_date = datetime.now().strftime("%Y-%m-%d")
        
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM disposal_records
            WHERE period_start <= ? AND (period_end >= ? OR period_end IS NULL)
            ORDER BY period_start DESC
        """, (ref_date, ref_date))

        columns = [desc[0] for desc in cursor.description]
        results = []
        for row in cursor.fetchall():
            results.append(dict(zip(columns, row)))

        return results


    def get_all_records(self, limit: int = None) -> List[Dict]:
        """取得所有處置紀錄"""
        cursor = self.conn.cursor()
        
        sql = "SELECT * FROM disposal_records ORDER BY announce_date DESC"
        if limit:
            sql += f" LIMIT {limit}"
        
        cursor.execute(sql)
        
        columns = [desc[0] for desc in cursor.description]
        results = []
        for row in cursor.fetchall():
            results.append(dict(zip(columns, row)))
        
        return results
    
    def get_statistics(self) -> Dict:
        """取得資料庫統計資訊"""
        cursor = self.conn.cursor()
        
        # 總筆數
        cursor.execute("SELECT COUNT(*) FROM disposal_records")
        total = cursor.fetchone()[0]
        
        # 各來源筆數
        cursor.execute("SELECT source, COUNT(*) FROM disposal_records GROUP BY source")
        by_source = dict(cursor.fetchall())
        
        # 不同股票數量
        cursor.execute("SELECT COUNT(DISTINCT code) FROM disposal_records")
        unique_stocks = cursor.fetchone()[0]
        
        return {
            "total_records": total,
            "by_source": by_source,
            "unique_stocks": unique_stocks
        }
    
    def update_custom_stats(self, code: str, period_raw: str, custom_stats_json: str) -> bool:
        """
        更新處置紀錄的手動統計數據 (Legacy, use update_custom_stats_by_id if possible)
        """
        try:
            cursor = self.conn.cursor()
            # Clean inputs
            code = code.strip()
            period_raw = period_raw.strip()
            
            cursor.execute("""
                UPDATE disposal_records 
                SET custom_stats = ?, updated_at = CURRENT_TIMESTAMP
                WHERE code = ? AND period_raw = ?
            """, (custom_stats_json, code, period_raw))
            
            if cursor.rowcount > 0:
                self.conn.commit()
                return True
            else:
                return False
                
        except Exception as e:
            print(f"Error in update_custom_stats: {e}")
            self.conn.rollback()
            return False

    def update_custom_stats_by_id(self, record_id: int, custom_stats_json: str) -> bool:
        """
        透過 ID 更新處置紀錄的手動統計數據 (推薦)
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                UPDATE disposal_records 
                SET custom_stats = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (custom_stats_json, record_id))
            
            if cursor.rowcount > 0:
                self.conn.commit()
                print(f"已更新手動統計 (ID: {record_id})")
                return True
            else:
                print(f"更新失敗 (ID: {record_id} not found)")
                return False
                
        except Exception as e:
            print(f"更新手動統計發生錯誤 (ID: {record_id}): {e}")
            self.conn.rollback()
            return False

    def delete_record(self, code: str, period_raw: str) -> bool:
        """
        刪除指定處置紀錄
        
        Args:
            code: 股票代號
            period_raw: 原始處置期間字串 (Composite Key part)
            
        Returns:
            bool: 是否成功刪除
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                DELETE FROM disposal_records 
                WHERE code = ? AND period_raw = ?
            """, (code, period_raw))
            
            if cursor.rowcount > 0:
                self.conn.commit()
                print(f"已刪除紀錄: {code}, {period_raw}")
                return True
            else:
                print(f"刪除失敗 (找不到紀錄): {code}, {period_raw}")
                return False
                
        except Exception as e:
            print(f"刪除紀錄發生錯誤: {e}")
            self.conn.rollback()
            return False
    
    def update_calculated_stats(self, record_id: int, stats_json: str) -> bool:
        """
        更新自動計算的統計數據緩存
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                UPDATE disposal_records 
                SET calculated_stats = ?
                WHERE id = ?
            """, (stats_json, record_id))
            
            if cursor.rowcount > 0:
                self.conn.commit()
                return True
            return False
        except Exception as e:
            print(f"Error caching stats: {e}")
            self.conn.rollback()
            return False

    def close(self):
        """關閉資料庫連線"""
        if self.conn:
            self.conn.close()


if __name__ == "__main__":
    # 初始化資料庫
    db = DisposalDatabase("data/disposal_history.db")
    
    # 匯入上櫃資料
    count_tpex = db.import_from_csv("publish2.csv", "上櫃", skip_rows=2)
    
    # 匯入上市資料
    count_twse = db.import_from_csv("punish.csv", "上市", skip_rows=2)
    
    # 顯示統計
    print(f"\n{'='*60}")
    print("資料庫統計")
    print(f"{'='*60}")
    stats = db.get_statistics()
    print(f"總筆數: {stats['total_records']}")
    print(f"各來源: {stats['by_source']}")
    print(f"不同股票數: {stats['unique_stocks']}")
    
    # 測試查詢
    print(f"\n{'='*60}")
    print("測試查詢: 2330")
    print(f"{'='*60}")
    records = db.get_disposal_by_code("2330")
    for r in records[:5]:  # 只顯示前 5 筆
        print(f"  {r['announce_date']} | {r['period_raw']} | {r['measure'][:30] if r['measure'] else 'N/A'}...")
    
    db.close()
    print("\n✓ 資料庫建立完成!")
