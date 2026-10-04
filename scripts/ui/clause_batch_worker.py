
from PyQt6.QtCore import QThread, pyqtSignal
from datetime import datetime, timedelta
import time
import random
from core.scraper_attention import AttentionScraper
from core.history_manager import HistoryManager
import re
import json

class ClauseBatchWorker(QThread):
    progress_update = pyqtSignal(str)
    finished_update = pyqtSignal(bool, str)
    
    def __init__(self, start_date, end_date):
        super().__init__()
        self.start_date = start_date
        self.end_date = end_date
        self.history_manager = HistoryManager()
        self.is_running = True
        
    def run(self):
        try:
            total_days = (self.end_date - self.start_date).days + 1
            processed = 0
            
            current = self.start_date
            while current <= self.end_date and self.is_running:
                date_str = current.strftime("%Y/%m/%d")
                self.progress_update.emit(f"正在下載 {date_str} 的注意條款...")
                
                try:
                    fetched_data = AttentionScraper.fetch_data(current)
                    
                    if fetched_data is not None:
                        # [Aggressive Sync REMOVED] - Do not delete local records based on potentially incomplete fetch results.
                        # This prevents wiping out "Official" data if the scraper returns a partial list or fails silently.
                        
                        count = 0
                        for item in fetched_data:
                            reason_text = item.get("reason", "")
                            # [Fix] Use strict parser (1-8 only) to match User Preference
                            trigger_val = self._parse_reason_text_strict(reason_text)
                            
                            date_key = current.strftime("%m/%d")
                            # date_key_full is already defined above
                            # trigger_info = {date_key: trigger_val}
                            # Wait, we should probably merge trigger_info if it exists?
                            # But for a batch download, we assume this is the source of truth for THIS date.
                            # So overwriting the "trigger" for this date is fine.
                            
                            trigger_info = {date_key: trigger_val}
                            
                            # Check if record already exists to PRESERVE Source
                            existing_rec = None
                            for h_rec in self.history_manager.history:
                                if h_rec.get("date") == date_key_full and str(h_rec.get("code")) == str(item["code"]):
                                    existing_rec = h_rec
                                    break
                            
                            final_source = item.get("source", "上市(條款)")
                            if existing_rec:
                                # Preserve existing source (so it stays in Blue Box if it was "上市")
                                final_source = existing_rec.get("source", final_source)
                                
                            # Construct Record
                            record = {
                                "date": date_key_full,
                                "code": item["code"],
                                "name": item["name"],
                                "reason": reason_text,
                                "source": final_source,
                                "trigger_info": json.dumps(trigger_info, ensure_ascii=False),
                                "is_disposed_next_day": False,
                                "tags": [],
                                "comment": ""
                            }
                            
                            # Update or Add
                            if existing_rec:
                                # Update fields but preserve tags/comments
                                existing_rec["name"] = record["name"]
                                existing_rec["reason"] = record["reason"]
                                existing_rec["source"] = record["source"]
                                existing_rec["trigger_info"] = record["trigger_info"]
                                # existing_rec["tags"] = ... (keep)
                                # existing_rec["comment"] = ... (keep)
                            else:
                                self.history_manager.add_record(record)
                                
                            count += 1
                        
                        self.history_manager.save()
                        self.progress_update.emit(f"  {date_str}: 成功更新 {count} 筆記錄")
                    else:
                        self.progress_update.emit(f"  {date_str}: 無資料 (或假日)")
                        
                except Exception as e:
                    self.progress_update.emit(f"  {date_str} 下載失敗: {e}")
                    import traceback
                    traceback.print_exc()
                
                # Delay (Reduced)
                delay = random.uniform(0.5, 0.8)
                time.sleep(delay)
                
                current += timedelta(days=1)
                processed += 1
                
            self.finished_update.emit(True, "下載完成")
            
        except Exception as e:
            self.finished_update.emit(False, str(e))

    def _parse_reason_text(self, text):
        """Deprecated: Use strict version"""
        return self._parse_reason_text_strict(text)

    def _parse_reason_text_strict(self, text):
        """Helper to extract standard clauses from reason text (1-8 ONLY)"""
        if not text: return ""
        
        # [Fix] Only allow 1-8 (一~八)
        # Exclude 9, 10, 11...
        matches = re.findall(r'第?\s*([1-8]|[一二三四五六七八])\s*款', text)
        if matches:
            # Normalize to Chinese numerals if needed? Or just pass through?
            # Dashboard expects Chinese for correct sorting/display usually?
            # Actually Dashboard.parse_clauses maps 1-8 to 一-八.
            # But let's verify what matches returns.
            # If text is "第一款", matches=["一"]
            # If text is "第1款", matches=["1"]
            return ",".join(matches)
            
        clauses = set()
        if "週轉" in text: clauses.add("四")
        
        if clauses: return ",".join(sorted(list(clauses)))
        
        # [Fix] If no clauses 1-8 found, return Empty string instead of "注意"
        # This prevents "注意" from being displayed as a confusing clause
        return ""

    def stop(self):
        self.is_running = False
