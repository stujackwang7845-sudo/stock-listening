"""
HistoricalDataRefreshWorker - 重新下載特定日期的完整資料

功能：
- 獲取處置公告（含處置頻率）
- 獲取融資融券清單
- 收集注意股資料
- 建立並儲存 agg_data 到快取

使用：
worker = HistoricalDataRefreshWorker(target_date)
worker.progress_update.connect(lambda msg: print(msg))
worker.data_ready.connect(self.on_historical_data_ready)
worker.error.connect(lambda msg: print(f"Error: {msg}"))
worker.start()
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timedelta
from PyQt6.QtCore import QThread, pyqtSignal
import re

# 確保可以導入專案模組
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from core.fetcher import StockFetcher
from core.parser import StockParser
from core.history_manager import HistoryManager
from core.cache import CacheManager


from core.scraper_attention import AttentionScraper

class HistoricalDataRefreshWorker(QThread):
    """重新下載特定歷史日期的完整資料"""
    
    progress_update = pyqtSignal(str)
    data_ready = pyqtSignal(dict, str)  # (agg_data, date_str)
    error = pyqtSignal(str)
    
    def __init__(self, target_date, lookback_days=14):
        """
        Args:
            target_date: datetime 目標日期
            lookback_days: int 向前收集注意股資料的天數（預設14天）
        """
        super().__init__()
        self.target_date = target_date
        self.lookback_days = lookback_days
        self.fetcher = StockFetcher()
        self.parser = StockParser()
        self.history_manager = HistoryManager()
        self.cache_manager = CacheManager()
        
    def run(self):
        try:
            target_date_str = self.target_date.strftime("%Y%m%d")
            display_date_str = self.target_date.strftime("%Y/%m/%d")
            
            self.progress_update.emit(f"開始下載 {display_date_str} 的資料...")
            
            # 步驟 1: 獲取處置資料（含頻率）
            self.progress_update.emit("正在獲取處置公告...")
            disposition_map = self._fetch_disposition_data(target_date_str)
            self.progress_update.emit(f"  找到 {len(disposition_map)} 筆處置資料")
            
            # 步驟 2: 獲取融資融券清單
            self.progress_update.emit("正在獲取融資融券清單...")
            margin_codes = self._fetch_margin_data(target_date_str)
            self.progress_update.emit(f"  找到 {len(margin_codes)} 支可融券股票")
            
            # 步驟 3: 收集注意股（從 API 獲取當天 + listening_history 補充歷史）
            self.progress_update.emit("正在收集注意股資料...")
            stock_dict = self._collect_attention_stocks()
            self.progress_update.emit(f"  收集到 {len(stock_dict)} 支注意股")
            
            # 步驟 3.5: 將當天的注意股寫入 listening_history.json
            # 重要：這樣未來查詢其他日期時才能補充歷史條款
            self.progress_update.emit("正在更新歷史記錄...")
            self._save_to_listening_history(stock_dict)
            
            # 步驟 4: 建立 agg_data
            self.progress_update.emit("正在建立資料結構...")
            agg_data = self._build_agg_data(stock_dict, disposition_map, margin_codes)
            
            # 步驟 5: 存入快取
            self.progress_update.emit("正在儲存到快取...")
            self.cache_manager.save_agg_data(target_date_str, agg_data)
            
            self.progress_update.emit(f"✓ 完成！共處理 {len(agg_data)} 支股票")
            
            # 發送完成信號
            self.data_ready.emit(agg_data, target_date_str)
            
        except Exception as e:
            error_msg = f"下載資料時發生錯誤: {str(e)}"
            print(f"[HistoricalDataRefreshWorker] {error_msg}")
            import traceback
            traceback.print_exc()
            self.error.emit(error_msg)
    
    def _fetch_disposition_data(self, date_str):
        """獲取處置資料（TWSE + TPEX）"""
        disposition_map = {}
        
        # TWSE 處置
        try:
            twse_disp = self.fetcher.fetch_twse_disposition(date_str)
            if twse_disp:
                parsed = self.parser.parse_twse_disposition(twse_disp)
                for item in parsed:
                    code = item["code"].strip()
                    disposition_map[code] = {
                        "name": item["name"],
                        "source": "上市",
                        "period": item.get("period", ""),
                        "measure": item.get("measure", ""),
                    }
        except Exception as e:
            print(f"[HistoricalDataRefreshWorker] TWSE 處置資料獲取失敗: {e}")
        
        # TPEX 處置
        try:
            tpex_disp = self.fetcher.fetch_tpex_disposition(date_str)
            if tpex_disp:
                parsed_otc = self.parser.parse_tpex_disposition(tpex_disp)
                for item in parsed_otc:
                    code = item["code"].strip()
                    disposition_map[code] = {
                        "name": item["name"],
                        "source": "上櫃",
                        "period": item.get("period", ""),
                        "measure": item.get("measure", ""),
                    }
        except Exception as e:
            print(f"[HistoricalDataRefreshWorker] TPEX 處置資料獲取失敗: {e}")

        # [Fallback/Supplement] 使用 Shioaji 補齊處置股 (TWSE API 經常阻擋)
        try:
            from core.shioaji_client import ShioajiClient
            from datetime import datetime
            sj = ShioajiClient()
            punish_df = sj.get_punish()
            if punish_df is not None and not punish_df.empty:
                for _, row in punish_df.iterrows():
                    p_code = str(row.get("code", "")).strip()
                    if p_code and p_code not in disposition_map:
                        # 嘗試解析起訖日
                        s_dt = row.get("start_date")
                        e_dt = row.get("end_date")
                        period = ""
                        if s_dt and e_dt:
                            try:
                                s_obj = datetime.strptime(str(s_dt), "%Y-%m-%d")
                                e_obj = datetime.strptime(str(e_dt), "%Y-%m-%d")
                                period = f"{s_obj.year-1911}/{s_obj.strftime('%m/%d')}~{e_obj.year-1911}/{e_obj.strftime('%m/%d')}"
                            except Exception:
                                pass
                        
                        interval = row.get("interval", "")
                        measure = f"{interval}分盤" if interval else "處置中"
                        
                        # 檢查上市或上櫃 (Shioaji 也有這兩者)
                        source = self.fetcher.check_market_type(p_code)
                        if not source: source = "上市" # 預設上市
                        
                        disposition_map[p_code] = {
                            "name": p_code, # 缺名稱，暫時用代號，UI可透過其他方式顯示
                            "source": source,
                            "period": period,
                            "measure": measure,
                        }
        except Exception as e:
            print(f"[HistoricalDataRefreshWorker] Shioaji 處置資料補充失敗: {e}")
        
        return disposition_map
    
    def _fetch_margin_data(self, date_str):
        """獲取融資融券清單（TWSE + TPEX）"""
        margin_codes = set()
        
        # TWSE
        try:
            twse_margin = self.fetcher.fetch_twse_margin_list(date_str)
            if twse_margin:
                margin_codes.update(self.parser.parse_twse_margin(twse_margin))
        except Exception as e:
            print(f"[HistoricalDataRefreshWorker] TWSE 融資融券獲取失敗: {e}")
        
        # TPEX
        try:
            tpex_margin = self.fetcher.fetch_tpex_margin_list(date_str)
            if tpex_margin:
                tpex_codes = self.parser.parse_tpex_margin(tpex_margin)
                margin_codes.update(tpex_codes)
        except Exception as e:
            print(f"[HistoricalDataRefreshWorker] TPEX 融資融券獲取失敗: {e}")
        
        return margin_codes
    
    def _collect_attention_stocks(self):
        """
        收集注意股
        
        修正：依照使用者需求，"聽牌區" 只從 listening_history.json (GitHub) 獲取資料
        且僅包含 [目標日期] 當天存在的股票。
        """
        stock_dict = {}
        
        target_date_str = self.target_date.strftime("%Y-%m-%d") # JSON uses hyphen
        
        # [Fix] 只有當 target_date 是「今天」時，才從 API 獲取最新資料。
        # 因為 AttentionScraper 只能獲取當日最新聽牌股，若對歷史日期呼叫，會把今天的資料寫入歷史，導致資料錯亂。
        api_records = []
        now_date = datetime.now().date()
        if self.target_date.date() >= now_date:
            print(f"[Worker] Fetching fresh data for {target_date_str} from API...")
            try:
                 api_records = AttentionScraper.fetch_data(self.target_date)
            except Exception as e:
                 print(f"[Worker] API Fetch Failed: {e}")
        else:
            print(f"[Worker] {target_date_str} is a past date, using local listening history only.")
        
        # Load local history to preserve comments/tags
        local_records = self.history_manager.get_listening_data(self.target_date)
        local_map = {r["code"]: r for r in local_records}
        
        # Merge API into Local (API is authority for status, Local for comments)
        # Verify API records are valid
        final_stock_dict = {}
        
        # Use API records as base if available
        source_records = api_records if api_records else local_records
        
        for r in source_records:
            code = r["code"]
            local_r = local_map.get(code, {})
            
            # Construct Trigger Info
            trigger_info = local_r.get("trigger_info", {})
            if isinstance(trigger_info, str):
                try: trigger_info = json.loads(trigger_info)
                except: trigger_info = {}
            
            # If API record, parse reason
            if r in api_records: # distinct object
                 reason_text = r.get("reason", "")
                 trigger_val = self._parse_reason_text(reason_text)
                 
                 date_key = self.target_date.strftime("%m/%d")
                 trigger_info[date_key] = trigger_val

            final_stock_dict[code] = {
                "name": r["name"],
                "source": r.get("source", "上市"), # API usually has source, fallback to listing
                "trigger_info": trigger_info,
                "has_futures": r.get("has_futures", False),
                "tags": local_r.get("tags", []),
                "comment": local_r.get("comment", "")
            }
            
            # full_rec = final_stock_dict[code].copy()
            # full_rec["code"] = code
            # full_rec["date"] = target_date_str
            # full_rec["reason"] = r.get("reason", "")
            # full_rec["is_disposed_next_day"] = local_r.get("is_disposed_next_day", False)
            # if isinstance(full_rec["trigger_info"], dict):
            #      full_rec["trigger_info"] = json.dumps(full_rec["trigger_info"], ensure_ascii=False)
            
            # [User Request] 不要在更新快取時自動寫入 listening_history.json，保持聽牌資料的純淨
            # self.history_manager.add_record(full_rec)

        # self.history_manager.save()
        stock_dict = final_stock_dict

        # 步驟 2: 補充歷史條款 (Lookback)
        start_date = self.target_date - timedelta(days=self.lookback_days)
        current = start_date
        
        self.progress_update.emit(f"正在讀取並修復歷史資料...")

        while current < self.target_date:
            records = self.history_manager.get_listening_data(current)
            
            # [Fix] 取消從 AttentionScraper.fetch_data(current) 回補歷史
            # 因為 AttentionScraper.fetch_data 無法接受歷史日期參數（只會回傳最新資料）
            # 這會導致把今天的聽牌股複製到過去的每一天，造成重複並拖慢下載速度。
            if not records:
                pass # 留空，不進行錯誤的回補

            if records:
                date_key = current.strftime("%m/%d")
                for r in records:
                    code = r["code"]
                    if code in stock_dict:
                        # 補充歷史條款
                        trigger_info = r.get("trigger_info", {})
                        if isinstance(trigger_info, str):
                             try: trigger_info = json.loads(trigger_info)
                             except: trigger_info = {}
                        
                        # [Fix] Repair "Bad Data"
                        val = trigger_info.get(date_key, "")
                        should_repair = False
                        if not val and r.get("reason"): should_repair = True
                        elif len(val) > 10: should_repair = True # Long text
                        elif "注意" in val or "Continuous" in val: should_repair = True 
                        
                        if should_repair:
                             reason_text = r.get("reason", "")
                             new_val = self._parse_reason_text(reason_text)
                             trigger_info[date_key] = new_val

                        stock_dict[code]["trigger_info"].update(trigger_info)
                        if r.get("has_futures"):
                            stock_dict[code]["has_futures"] = True
            
            current += timedelta(days=1)
            
        return stock_dict

    def _parse_reason_text(self, text):
        """Helper to extract standard clauses from reason text"""
        if not text: return ""
        
        # 1. Regex for standard "第X款"
        matches = re.findall(r'第?\s*([0-9]+|[一二三四五六七八九十]+)\s*款', text)
        if matches:
            return ",".join(matches)
            
        # 2. TPEX Keywords Fallback
        clauses = set()
        if "連續" in text or "累積" in text:
            clauses.add("一")
        if "週轉" in text:
            clauses.add("四") # Common attention clause
            
        if clauses:
            return ",".join(sorted(list(clauses)))
            
        return "注意"
    
    def _build_agg_data(self, stock_dict, disposition_map, margin_codes):
        """建立 agg_data 結構"""
        agg_data = {}
        
        # 處理注意股
        for code, info in stock_dict.items():
            is_disposed = code in disposition_map
            can_short = code in margin_codes
            
            agg_data[code] = {
                "code": code,
                "name": info["name"],
                "source": info["source"],
                "is_disposed": is_disposed,
                "period": disposition_map[code]["period"] if is_disposed else "",
                "measure": disposition_map[code]["measure"] if is_disposed else "",
                "clauses": info["trigger_info"],
                "has_futures": info["has_futures"],
                "can_short": can_short,
            }
        
        # 確保處置股都在（即使不在注意股清單中）
        for code, info in disposition_map.items():
            if code not in agg_data:
                can_short = code in margin_codes
                # [Fix] 從靜態清單確認期貨status，避免硬編碼False
                try:
                    from core.futures_stocks import has_futures as check_futures
                    hf = check_futures(code)
                except Exception:
                    hf = False
                
                agg_data[code] = {
                    "code": code,
                    "name": info["name"],
                    "source": info["source"],
                    "is_disposed": True,
                    "period": info["period"],
                    "measure": info["measure"],
                    "clauses": {},
                    "has_futures": hf,
                    "can_short": can_short,
                }
        
        return agg_data
    
    def _save_to_listening_history(self, stock_dict):
        """[Disabled] 為避免污染官方聽牌紀錄，不再自動將合併後的注意股寫入 listening_history.json"""
        pass
