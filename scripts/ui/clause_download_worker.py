"""
注意條款下載 Worker

背景執行注意條款的下載與儲存工作
"""

from PyQt6.QtCore import QThread, pyqtSignal
from datetime import datetime, timedelta
from typing import List
import sqlite3

from core.scraper_attention import AttentionScraper
from core.scraper_twse_notice import NoticeFetcher
from core.clause_parser import ClauseNumberParser
from core import database_clause_ext


class ClauseDownloadWorker(QThread):
    """注意條款下載 Worker"""
    
    progress = pyqtSignal(str)  # 進度訊息
    finished = pyqtSignal(bool, str)  # (成功, 訊息)
    
    def __init__(self, dates: List[datetime], db_path="data/disposal_history.db"):
        super().__init__()
        self.dates = dates
        self.db_path = db_path
    
    def run(self):
        """執行下載任務"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            total = len(self.dates)
            all_records = []
            
            for idx, date in enumerate(self.dates):
                date_str = date.strftime('%Y/%m/%d')
                self.progress.emit(f"下載 {date_str} ({idx+1}/{total})...")
                
                # 下載資料 (TWSE + TPEX 有款次的正式注意交易資訊)
                data = NoticeFetcher.fetch_notice(date)
                
                if not data:
                    print(f"DEBUG: {date_str} 無注意股資料")
                    continue
                
                print(f"DEBUG: {date_str} 下載到 {len(data)} 筆注意股")
                
                # 處理每筆資料
                for item in data:
                    code = item.get('code', '').strip()
                    
                    # [NEW] 過濾：只保留 4 碼股票（排除權證）
                    if not code or len(code) != 4:
                        continue
                    
                    reason = item.get('reason', '')
                    clauses = ClauseNumberParser.parse(reason)
                    
                    # [Fix] 沒有具體條款但出現在注意股名單 = 用 clause_number=0 代表「注意（無具體條款）」
                    # 解決「已有X次」累計描述型注意紀錄（如 3081）被忽略的問題
                    if not clauses:
                        clauses = [0]  # 0 = 注意但無具體 1-8 款
                    
                    for clause_num in clauses:
                        all_records.append({
                            'announce_date': date.strftime('%Y-%m-%d'),
                            'code': code,
                            'name': item.get('name', ''),
                            'source': item.get('source', 'TWSE'),
                            'clause_number': clause_num,
                            'reason': reason
                        })
            
            # 儲存到資料庫
            if all_records:
                saved_count = database_clause_ext.save_attention_clauses(conn, all_records)
                conn.close()
                
                msg = f"成功下載並儲存 {saved_count} 筆條款資料（來自 {len(self.dates)} 個交易日）"
                self.finished.emit(True, msg)
            else:
                conn.close()
                msg = f"在 {len(self.dates)} 個交易日中未找到符合條件的注意條款（1-8款、4碼股票）"
                self.finished.emit(True, msg)
            
        except Exception as e:
            if conn:
                conn.close()
            error_msg = f"下載失敗：{str(e)}"
            print(f"Error in ClauseDownloadWorker: {error_msg}")
            self.finished.emit(False, error_msg)
