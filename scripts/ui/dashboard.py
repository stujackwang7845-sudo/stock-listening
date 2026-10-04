
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTableWidget, 
                             QTableWidgetItem, QHeaderView, QLabel, QFrame, QTextEdit, 
                             QGridLayout, QScrollArea, QAbstractItemView, QMessageBox,
                             QPushButton, QCalendarWidget, QDialog, QLineEdit, QApplication)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QDate
from PyQt6.QtGui import QColor, QBrush, QFont
from datetime import datetime
import datetime as dt # Alienate to avoid collision
import pandas as pd
import json
import time
from core.utils import DateUtils, ClauseParser
from core.fetcher import StockFetcher
from core.parser import StockParser
from core.cache import CacheManager
from core.predictor import DispositionPredictor
from core.history_manager import HistoryManager
from ui.history_page import HistoryPage
from core.scraper_attention import AttentionScraper
from core.search_helper import StockSearchHelper
from ui.historical_refresh_worker import HistoricalDataRefreshWorker
from core.disposal_database import DisposalDatabase
from core.price_database import PriceDatabase
from ui.clause_batch_worker import ClauseBatchWorker
from core.margin_futures_db import MarginFuturesDatabase

class SortableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        # 1. Try UserRole (Explicit Numeric)
        try:
            self_val = self.data(Qt.ItemDataRole.UserRole)
            other_val = other.data(Qt.ItemDataRole.UserRole)
            
            if self_val is not None and other_val is not None:
                return float(self_val) < float(other_val)
        except:
            pass

        # 2. Try DisplayRole (Numeric String?)
        try:
            self_val = float(self.data(Qt.ItemDataRole.DisplayRole))
            other_val = float(other.data(Qt.ItemDataRole.DisplayRole))
            return self_val < other_val
        except (ValueError, TypeError, AttributeError):
            pass
            
        # 3. Fallback to string comparison
        return super().__lt__(other)

class InfoBox(QFrame):
    item_clicked = pyqtSignal(str) # Signal for link clicks

    def __init__(self, title, items_dict=None, color_theme="#4da6ff", header_btn_text=None, header_btn_callback=None):
        super().__init__()
        self.setObjectName("ObserverBox")
        self.setStyleSheet(f"""
            QFrame#ObserverBox {{
                background-color: #252526;
                border: 2px solid {color_theme};
                border-radius: 8px;
            }}
            QLabel {{ color: #E0E0E0; font-size: 14px; }}
            QLabel#title {{ color: {color_theme}; font-weight: bold; font-size: 16px; margin-bottom: 5px; }}
        """)
        
        layout = QVBoxLayout(self)
        
        # Header Layout (Title + Optional Button)
        header_layout = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setObjectName("title")
        header_layout.addWidget(title_lbl)
        
        if header_btn_text and header_btn_callback:
            header_layout.addStretch()
            btn = QPushButton(header_btn_text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(header_btn_callback)
            header_layout.addWidget(btn)
            
        layout.addLayout(header_layout)
        
        self.content_layout = QVBoxLayout()
        layout.addLayout(self.content_layout)
        
        if items_dict:
            self.update_items(items_dict)
            
    def update_items(self, items_data):
        # Clear existing layout safely
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                # Recursively delete layout items
                sub_layout = item.layout()
                while sub_layout.count():
                    sub_item = sub_layout.takeAt(0)
                    if sub_item.widget():
                        sub_item.widget().deleteLater()
                sub_layout.deleteLater()
            
        # Normalize to list of (key, val)
        if isinstance(items_data, dict):
            iterator = items_data.items()
        else:
            iterator = items_data
            
        for key, val in iterator:
            row = QHBoxLayout()
            
            # Header Mode (No Value)
            if val is None or val == "":
                # Treated as Section Header
                key_lbl = QLabel(key)
                key_lbl.setStyleSheet("color: #FFD700; font-weight: bold; font-size: 15px; margin-top: 5px;")
                row.addWidget(key_lbl)
            else:
                # Standard Key-Value
                # Ensure colon if not present, but respect spaces for indentation if any
                clean_key = key.strip()
                if clean_key and not key.endswith(":"):
                    display_key = f"{key}:"
                else:
                    display_key = key
                    
                key_lbl = QLabel(display_key)
                # Keep original color or use theme
                key_lbl.setStyleSheet("color: #4da6ff; font-weight: bold;")
                row.addWidget(key_lbl)
                
                val_lbl = QLabel(str(val))
                val_lbl.setWordWrap(True)
                val_lbl.setOpenExternalLinks(False) # Catch link clicks
                val_lbl.linkActivated.connect(self.item_clicked.emit)
                row.addWidget(val_lbl, 1) # Expand
                
            self.content_layout.addLayout(row)
        self.content_layout.addStretch()

class ListeningFetchWorker(QThread):
    """
    [Fix 2026-09-01] 只做「聽牌(官方)」的網路抓取，不碰 history_manager/UI。

    start_worker() 遇到當天已有 agg_cache 時會直接沿用快取並提前 return，完全
    跳過 HistoryWorker（包含它裡面的注意股自動抓取），而 HistoryWorker 本身
    又只在晚上 7-11 點這個窗口才會被 force_refresh 觸發——這兩層疊加造成「白天
    開軟體、且當天已有快取」時，聽牌名單永遠不會自動更新，只能手動按更新按鈕。
    這支 worker 獨立於上述兩個閘門之外，開軟體時一律執行，且只抓一次 API
    （很輕量，不是 HistoryWorker 那種 14 天回補），確保聽牌區不用手動更新。
    只做網路 I/O，寫入 history_manager 與刷新畫面留在主執行緒做（history_manager
    是跟 dashboard/forecast_page 共用的同一個物件，避免背景執行緒直接寫入）。
    """
    fetched = pyqtSignal(list)

    def __init__(self, target_date):
        super().__init__()
        self.target_date = target_date

    def run(self):
        att_list = []
        try:
            from core.scraper_attention import AttentionScraper
            att_list = AttentionScraper.fetch_data(self.target_date) or []
        except Exception as e:
            print(f"[ListeningFetchWorker] Error: {e}")
        self.fetched.emit(att_list)


class ClausesGapFetchWorker(QThread):
    """
    [Fix 2026-09-03] 開軟體時自動回補 attention_clauses 表缺的資料，取代原本只能
    靠「下載注意條款」按鈕手動觸發的方式(見 dashboard.py 的
    auto_refresh_clauses_on_startup)。自己開一個獨立的 sqlite3 連線做網路抓取+
    寫入(不共用 UI 執行緒的連線)，跟 ClauseDownloadWorker 用同一套抓取/儲存邏輯，
    只是這裡會先查表裡目前最新的公告日，只回補真正缺的天數，避免每次開軟體都
    重抓整個 8-9 天的預設範圍。
    """
    finished_ok = pyqtSignal(int, str)  # (saved_count, error_msg)

    def __init__(self, target_date, db_path="data/disposal_history.db"):
        super().__init__()
        self.target_date = target_date
        self.db_path = db_path

    def run(self):
        import sqlite3
        from datetime import timedelta
        from core.scraper_twse_notice import NoticeFetcher
        from core.clause_parser import ClauseNumberParser
        from core import database_clause_ext
        from core.utils import DateUtils

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 找出表裡目前最新的公告日，只回補「最新公告日+1」到「顯示日期」這段
            cursor.execute("SELECT MAX(announce_date) FROM attention_clauses")
            row = cursor.fetchone()
            latest = row[0] if row else None

            target_date_only = self.target_date.date() if hasattr(self.target_date, 'date') else self.target_date

            if latest:
                try:
                    start_date = datetime.strptime(latest, "%Y-%m-%d").date() + timedelta(days=1)
                except Exception:
                    start_date = None
            else:
                start_date = None

            if start_date is None:
                # 表是空的或解析失敗，退回跟手動按鈕一樣的預設範圍(往前 8 個交易日)
                dates = []
                if DateUtils.is_trading_day(target_date_only):
                    dates.append(target_date_only)
                count = 0
                test_date = target_date_only - timedelta(days=1)
                while count < 8:
                    if DateUtils.is_trading_day(test_date):
                        dates.append(test_date)
                        count += 1
                    test_date -= timedelta(days=1)
                dates.reverse()
            else:
                dates = []
                d = start_date
                while d <= target_date_only:
                    if DateUtils.is_trading_day(d):
                        dates.append(d)
                    d += timedelta(days=1)

            if not dates:
                conn.close()
                self.finished_ok.emit(0, "")
                return

            all_records = []
            for date in dates:
                date_dt = datetime(date.year, date.month, date.day)
                data = NoticeFetcher.fetch_notice(date_dt)
                if not data:
                    continue
                for item in data:
                    code = item.get('code', '').strip()
                    if not code or len(code) != 4:
                        continue
                    reason = item.get('reason', '')
                    clauses = ClauseNumberParser.parse(reason)
                    if not clauses:
                        clauses = [0]
                    for clause_num in clauses:
                        all_records.append({
                            'announce_date': date.strftime('%Y-%m-%d'),
                            'code': code,
                            'name': item.get('name', ''),
                            'source': item.get('source', 'TWSE'),
                            'clause_number': clause_num,
                            'reason': reason
                        })

            saved_count = database_clause_ext.save_attention_clauses(conn, all_records) if all_records else 0
            conn.close()
            self.finished_ok.emit(saved_count, "")
        except Exception as e:
            self.finished_ok.emit(0, str(e))


class HistoryWorker(QThread):
    data_ready = pyqtSignal(dict) # aggregated data
    progress_update = pyqtSignal(str)
    
    def __init__(self, dates_to_fetch, target_date=None, history_manager=None):
        super().__init__()
        self.dates_to_fetch = dates_to_fetch 
        self.cache_mgr = CacheManager()
        self.target_date = target_date
        self.history_manager = history_manager

    def run(self):
        fetcher = StockFetcher()
        parser = StockParser()
        
        agg_data = {}
        
        # Determine Date Context
        if self.target_date:
            target_dt = self.target_date
            self.progress_update.emit(f"正在抓取 {target_dt.strftime('%Y/%m/%d')} 資料...")
        else:
            target_dt = DateUtils.get_last_trading_day()
            
        target_date_str = target_dt.strftime("%Y%m%d")

        # 啟動時一併同步聽牌資料庫 + 處置公告 (依時間決定來源)
        if self.history_manager:
            try:
                from datetime import datetime
                now = datetime.now()
                # 無論何時啟動，一律優先進行雙軌聯集抓取，確保最新資料正確無誤
                if True:
                    self.progress_update.emit("正在下載今日最新注意股票...")
                    merged_records = {} # code -> record_dict
                    
                    # === 1. (已移除) 嘗試從永豐 API 抓取注意股 ===
                    # 依據使用者規則：聽牌區清單永遠來自官方跟GITHUB，不用自己計算。
                    # Shioaji 的 notice_df 會包含大量非官方聽牌的標的，導致 listening_history.json 被污染。
                    
                    # === 2. 執行傳統網頁爬蟲 (雙軌聯集備援) ===
                    try:
                        att_list = AttentionScraper.fetch_data(target_dt)
                        if att_list:
                            for item in att_list:
                                code = item['code'].strip()
                                if len(code) > 4 or '.' in code:
                                    continue
                                reason_str = item.get('reason', '')
                                date_key = target_dt.strftime("%m/%d")
                                parsed_clause = ClauseParser.parse_clauses(reason_str)
                                
                                # 取得正確的市場來源
                                raw_source = item.get('source', '')
                                if raw_source in ("TWSE", "tse", "上市"):
                                    market_src = "上市"
                                elif raw_source in ("TPEX", "otc", "OTC", "上櫃"):
                                    market_src = "上櫃"
                                else:
                                    market_src = fetcher.check_market_type(code) or "上市"
                                
                                # 合併：如果爬蟲有更豐富的資訊 (如名稱)，以此為主
                                if code in merged_records:
                                    merged_records[code]["name"] = item['name']
                                    if not merged_records[code]["reason"] and reason_str:
                                        merged_records[code]["reason"] = reason_str
                                        merged_records[code]["trigger_info"] = json.dumps({date_key: parsed_clause}, ensure_ascii=False) if parsed_clause else "{}"
                                else:
                                    merged_records[code] = {
                                        "date": target_dt.strftime("%Y-%m-%d"),
                                        "code": code,
                                        "name": item['name'],
                                        "reason": reason_str,
                                        "source": market_src,
                                        "trigger_info": json.dumps({date_key: parsed_clause}, ensure_ascii=False) if parsed_clause else "{}",
                                        "is_disposed_next_day": False,
                                        "tags": [],
                                        "comment": ""
                                    }
                            print(f"[HistoryWorker] 爬蟲: 已抓取 {len(att_list)} 筆注意股")
                    except Exception as e:
                        print(f"[HistoryWorker] 爬蟲注意股抓取失敗: {e}")
                    
                    # === 3. 寫入本地歷史資料庫 ===
                    if merged_records:
                        for record in merged_records.values():
                            self.history_manager.add_record(record)
                        print(f"[HistoryWorker] 聯集成功，共存入/更新 {len(merged_records)} 筆官方注意股")
                
                # 聯集抓取完畢後，統一從 GitHub 同步歷史，補齊其他天數的缺失資料
                self.progress_update.emit("正在同步官方聽牌資料庫 (GitHub)...")
                self.history_manager.sync_from_github()
                
                # === 自動下載處置公告（所有時段都執行）===
                try:
                    self.progress_update.emit("正在自動更新處置公告...")
                    # 優先嘗試 Shioaji api.punish()
                    punish_saved = False
                    try:
                        from core.shioaji_client import ShioajiClient
                        sj_client = ShioajiClient()
                        punish_df = sj_client.get_punish()
                        if punish_df is not None and not punish_df.empty:
                            from core.disposal_database import DisposalDatabase
                            disp_db = DisposalDatabase()
                            cursor = disp_db.conn.cursor()
                            count = 0
                            for _, p_row in punish_df.iterrows():
                                try:
                                    p_code = str(p_row.get('code', '')).strip()
                                    p_start = p_row.get('start_date')
                                    p_end = p_row.get('end_date')
                                    p_interval = str(p_row.get('interval', ''))
                                    p_desc = str(p_row.get('description', ''))
                                    p_announced = p_row.get('announced_date')
                                    
                                    start_str = p_start.strftime("%Y-%m-%d") if p_start else ""
                                    end_str = p_end.strftime("%Y-%m-%d") if p_end else ""
                                    announce_str = p_announced.strftime("%Y-%m-%d") if p_announced else ""
                                    
                                    # 動態識別上市/上櫃
                                    market_src = fetcher.check_market_type(p_code)
                                    if market_src not in ("上市", "上櫃"):
                                        market_src = "上市" # 預設安全值
                                    
                                    cursor.execute("""
                                        INSERT OR REPLACE INTO disposal_records
                                        (source, announce_date, code, name, period_start, period_end, 
                                         period_raw, measure, reason, updated_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                                    """, (
                                        market_src,
                                        announce_str,
                                        p_code,
                                        "",  # Shioaji 不提供名稱
                                        start_str,
                                        end_str,
                                        f"{start_str}~{end_str}",
                                        p_interval,
                                        p_desc
                                    ))
                                    count += 1
                                except Exception:
                                    pass
                            disp_db.conn.commit()
                            disp_db.close()
                            punish_saved = True
                            print(f"[HistoryWorker] Shioaji punish: 已存入 {count} 筆處置記錄")
                    except Exception as e:
                        print(f"[HistoryWorker] Shioaji punish 失敗: {e}")
                    
                    # 備援：若 Shioaji 失敗，用傳統爬蟲
                    if not punish_saved:
                        from core.disposal_database import DisposalDatabase
                        disp_db = DisposalDatabase()
                        cursor = disp_db.conn.cursor()
                        
                        # 2.1 上市爬取
                        twse_disp = fetcher.fetch_twse_disposition(target_date_str)
                        if twse_disp:
                            parsed_tse = parser.parse_twse_disposition(twse_disp)
                            if parsed_tse:
                                for item in parsed_tse:
                                    try:
                                        start, end = disp_db.parse_period(item.get("period"))
                                        cursor.execute("""
                                            INSERT OR REPLACE INTO disposal_records
                                            (source, announce_date, code, name, period_start, period_end,
                                             period_raw, measure, reason, updated_at)
                                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                                        """, (
                                            "上市", item.get("date") or start, item.get("code"), item.get("name"),
                                            start, end, item.get("period"), item.get("measure"), item.get("reason")
                                        ))
                                    except:
                                        pass
                                print(f"[HistoryWorker] 爬蟲: 已存入 {len(parsed_tse)} 筆上市處置記錄")
                                
                        # 2.2 上櫃爬取
                        tpex_disp = fetcher.fetch_tpex_disposition(target_date_str)
                        if tpex_disp:
                            parsed_otc = parser.parse_tpex_disposition(tpex_disp)
                            if parsed_otc:
                                for item in parsed_otc:
                                    try:
                                        start, end = disp_db.parse_period(item.get("period"))
                                        cursor.execute("""
                                            INSERT OR REPLACE INTO disposal_records
                                            (source, announce_date, code, name, period_start, period_end,
                                             period_raw, measure, reason, updated_at)
                                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                                        """, (
                                            "上櫃", item.get("date") or start, item.get("code"), item.get("name"),
                                            start, end, item.get("period"), item.get("measure"), item.get("reason")
                                        ))
                                    except:
                                        pass
                                print(f"[HistoryWorker] 爬蟲: 已存入 {len(parsed_otc)} 筆上櫃處置記錄")
                        
                        disp_db.conn.commit()
                        disp_db.close()
                except Exception as e:
                    print(f"[HistoryWorker] 自動更新處置公告失敗: {e}")
                    
            except Exception as e:
                print(f"[HistoryWorker] Sync error: {e}")
        
        # 0. Fetch Margin/Short Lists (Use Target Date)
        self.progress_update.emit("正在抓取融券/信用交易名單...")
        margin_codes = set()
        
        # Check if DateUtils supports historical margin lookup or if fetcher does
        # Currently fetcher methods take date_str
        margin_date_str = target_date_str
        
        # TWSE
        twse_margin = fetcher.fetch_twse_margin_list(margin_date_str)
        if twse_margin:
            margin_codes.update(parser.parse_twse_margin(twse_margin))
            
        # TPEX
        tpex_margin = fetcher.fetch_tpex_margin_list(margin_date_str)
        if tpex_margin:
            margin_codes.update(parser.parse_tpex_margin(tpex_margin))
            
        # Futures List
        self.progress_update.emit("正在抓取股票期貨清單...")
        futures_codes = set()
        taifex_futures = fetcher.fetch_taifex_futures_list()
        if taifex_futures:
            futures_codes.update(parser.parse_taifex_futures_list(taifex_futures))
            
        # 1. Fetch Current Disposition List (For Pink Highlight)
        self.progress_update.emit(f"正在抓取 {target_date_str} 處置股名單...")
        disposition_map = {} # code -> {name, source}
        
        # TWSE Disposition
        twse_disp = fetcher.fetch_twse_disposition(target_date_str)
        if twse_disp:
            parsed = parser.parse_twse_disposition(twse_disp)
            print(f"DEBUG: Parsed TWSE Disposition Items: {len(parsed)}")
            for item in parsed:
                code = item["code"].strip()
                if code not in disposition_map:
                    disposition_map[code] = []
                disposition_map[code].append({
                    "name": item["name"], 
                    "source": "上市",
                    "period": item.get("period", ""),
                    "measure": item.get("measure", "")
                })

        # TPEX Disposition
        self.progress_update.emit("正在抓取上櫃處置股...")
        tpex_disp = fetcher.fetch_tpex_disposition(target_date_str)
        if tpex_disp:
            parsed_otc = parser.parse_tpex_disposition(tpex_disp)
            print(f"DEBUG: Parsed TPEX Disposition Items: {len(parsed_otc)}")
            for item in parsed_otc:
                code = item["code"].strip()
                if code not in disposition_map:
                    disposition_map[code] = []
                disposition_map[code].append({
                    "name": item["name"], 
                    "source": "上櫃",
                    "period": item.get("period", ""),
                    "measure": item.get("measure", "")
                })
        
        print(f"DEBUG: Final Disposition Map Keys: {list(disposition_map.keys())}") 
        
        # Determine Fetch Dates (10 days history for prediction)
        last_trading_day = DateUtils.get_last_trading_day()
        
        days_to_fetch = []
        days_to_fetch.append(last_trading_day)
        
        count = 0
        curr = last_trading_day
        while count < 10:
            curr = curr - dt.timedelta(days=1)
            if DateUtils.is_trading_day(curr):
                days_to_fetch.append(curr)
                count += 1
        days_to_fetch.sort()
        
        self.progress_update.emit(f"準備抓取 {len(days_to_fetch)} 天資料...")
        
        for day in days_to_fetch:
            day_str = day.strftime("%Y%m%d")
            display_date = day.strftime("%m/%d")
            
            is_latest = (day == last_trading_day)
            cached_data = self.cache_mgr.get_daily_data(day_str)
            
            daily_items = []
            
            if cached_data is not None and not is_latest:
                self.progress_update.emit(f"讀取快取 {display_date} 資料...")
                daily_items = cached_data
            else:
                self.progress_update.emit(f"正在下載 {display_date} 資料...")
                
                # TWSE
                twse_data = fetcher.fetch_twse_attention(day_str)
                parsed_twse = parser.parse_twse_attention(twse_data)

                # TPEX
                tpex_data = fetcher.fetch_tpex_attention(day_str)
                parsed_tpex = parser.parse_tpex_attention(tpex_data)

                # Merge
                daily_items = parsed_twse + parsed_tpex
                print(f"DEBUG: Fetched {day_str} -> TWSE: {len(parsed_twse)}, TPEX: {len(parsed_tpex)}, Total: {len(daily_items)}", flush=True)

                # [Fix] fetch_twse_attention/fetch_tpex_attention 連線失敗時回傳 None（區別於「當天真的沒有注意股」
                # 的合法空清單）。若任一來源是 None，代表這天的資料不完整，不可快取，否則之後只讀快取、
                # 永遠不會重抓，會永久遺漏那一天某個市場的注意股條款（曾發生 TWSE 失敗、TPEX 成功，
                # 混出來的 19 筆被當成當天完整資料快取，導致該日某檔股票的第一款紀錄永久消失）。
                fetch_incomplete = (twse_data is None) or (tpex_data is None)

                # Save to Cache (Only if data found or not latest to avoid caching empty pending data)
                if fetch_incomplete:
                    print(f"DEBUG: Skipping cache save for {day_str} (Incomplete fetch: TWSE={'OK' if twse_data is not None else 'FAILED'}, TPEX={'OK' if tpex_data is not None else 'FAILED'})", flush=True)
                elif daily_items:
                    self.cache_mgr.save_daily_data(day_str, daily_items)
                elif not is_latest:
                     # If not latest and empty, maybe it's a holiday or really empty? Save to avoid repeated fetch.
                     self.cache_mgr.save_daily_data(day_str, daily_items)
                else:
                     print(f"DEBUG: Skipping cache save for {day_str} (Latest & Empty)", flush=True)
                
                QThread.msleep(500)
            
            print(f"DEBUG: Day {day_str} Items: {len(daily_items)}", flush=True)
            
            for item in daily_items:
                if item.get('code') in ('6230', '6442'):
                     print(f"Found {item.get('code')} in daily_items for {day_str}!", flush=True)
                code = item.get('code', '').strip()
                if not code: continue
                
                name = item.get('name', '')
                source = "上市" if item.get('source', '') == 'TWSE' else "上櫃"
                raw_reason = str(item.get('reason', ''))
                
                # DEBUG 8046 Raw Reason
                if code == '8046':
                    print(f"DEBUG 8046 Raw Reason: '{raw_reason}'", flush=True)

                clause = ClauseParser.parse_clauses(raw_reason)
                
                # [Fallback] Parse Future Disposal from Reason
                # Pattern: 115年01月27日至115年02月09日
                future_pd_found = None
                future_meas_found = "處置(詳見注意資訊)"
                try:
                    import re
                    # Match dates: 115年01月27日 ... 115年02月09日 OR 115/01/27 ... 115/02/09
                    # Regex handles "至" or "~" or "～" details
                    # Capture groups: Y1, M1, D1 ... Y2, M2, D2
                    # New Robust Regex: (\d{3})[年/](\d{1,2})[月/](\d{1,2})[日\s]*?[~至\-\—]+?[ ]*?(\d{3})[年/](\d{1,2})[月/](\d{1,2})[日\s]*?
                    date_pat = r"(\d{3})[年/](\d{1,2})[月/](\d{1,2})[日]?.*?[~至\-\—].*?(\d{3})[年/](\d{1,2})[月/](\d{1,2})[日]?"
                    m = re.search(date_pat, raw_reason)
                    if m:
                        y1, m1, d1 = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        y2, m2, d2 = int(m.group(4)), int(m.group(5)), int(m.group(6))
                        
                        start_dt = datetime(y1 + 1911, m1, d1)
                        end_dt = datetime(y2 + 1911, m2, d2)
                        
                        # Only consider if Start Date > Target Date (Future)
                        # We use self.target_date or last_trading_day
                        tgt = self.target_date if self.target_date else DateUtils.get_last_trading_day()
                        tgt_dt = datetime(tgt.year, tgt.month, tgt.day)
                        
                        if start_dt > tgt_dt:
                             p_str = f"{y1}/{m1:02d}/{d1:02d}~{y2}/{m2:02d}/{d2:02d}"
                             future_pd_found = p_str
                except Exception as e:
                    pass # Regex fail safe

                
                if code not in agg_data:
                    # Only add if it has recent hits
                    agg_data[code] = {
                        "name": name,
                        "source": source,
                        "clauses": {},
                        "disposition": None,
                        "can_short": (code in margin_codes),
                        "has_futures": (code in futures_codes)
                    }
                    if code == '2408':
                         pass # print(f"DEBUG: 2408 Added to agg_data.", flush=True)

                # Update Clause
                agg_data[code]["clauses"][display_date] = clause
                if code in ('6230', '6442'):
                    print(f'UPDATE CLAUSE INSIDE HISTORY WORKER: {code} -> {display_date} = {clause}')
                if code in ('6230', '6442'):
                    print(f'UPDATE CLAUSE INSIDE HISTORY WORKER: {code} -> {display_date} = {clause}')
                
                # Update Future Period from Attention (Fallback)
                if future_pd_found:
                    # Don't overwrite if already has better info (unlikely here)
                    if not agg_data[code].get("future_period"):
                        agg_data[code]["future_period"] = future_pd_found
                        agg_data[code]["future_measure"] = future_meas_found
                
            # Log all codes found for this day
            found_codes_list = [item.get('code','').strip() for item in daily_items]
            # print(f"DEBUG: Codes found in {day_str}: {found_codes_list}", flush=True)
        
        # --- Post-Processing: Apply Disposition Status & Add Missing Disposed Stocks ---
        # Modified Logic: Handle Multiple Dispositions (Active vs Future)
        if '8046' in disposition_map:
             pass 
        else:
             pass

        for code, info_list in disposition_map.items():
            # 1. Separate Active and Future
            active_info = None
            future_info = None
            
            # Use specific target date for comparison
            compare_date = self.target_date if self.target_date else DateUtils.get_last_trading_day()
            compare_date_dt = datetime(compare_date.year, compare_date.month, compare_date.day)
            
            # Sort info_list by period start to handle logic deterministically?
            # Actually, just find the one that covers Today (Active) and the one starting After Today (Future)
            
            for info in info_list:
                p_start = DateUtils.parse_period_start(info["period"])
                p_end = DateUtils.parse_period_end(info["period"])
                
                if p_start and p_end:
                    if p_start <= compare_date_dt <= p_end:
                        # Prioritize newer active disposition
                        if not active_info:
                            active_info = info
                        else:
                            curr_start = DateUtils.parse_period_start(active_info["period"])
                            if p_start > curr_start:
                                active_info = info
                    elif p_start > compare_date_dt:
                        # Prioritize earlier future disposition (next to come)
                        if not future_info:
                            future_info = info
                        else:
                            curr_future_start = DateUtils.parse_period_start(future_info["period"])
                            if p_start < curr_future_start:
                                future_info = info
            
            # Fallback: If only one record and it creates no active/future hit (maybe data error), 
            # assume it's active or future based on logic? 
            # Current logic previously just took the last one.
            # If we didn't match anything perfectly, maybe dates are slightly off or parser failed.
            # Let's trust parser dates.
            
            # If NO active found, but we have records, maybe the "Active" one ended yesterday? 
            # Or maybe it starts tomorrow?
            if not active_info and not future_info and info_list:
                # Fallback: Just take the first one?
                # Let's try to match loosely.
                pass

            # Update agg_data
            if code not in agg_data:
                # Determine base info from Active, or Future, or first available
                base = active_info if active_info else (future_info if future_info else info_list[0])
                
                agg_data[code] = {
                    "name": base["name"],
                    "source": base["source"],
                    "can_short": (code in margin_codes),
                    "has_futures": (code in futures_codes),
                    "clauses": {}, # No clauses history
                    "is_disposed": False, # Will set below
                    "period": "",
                    "measure": ""
                }
            
            # Apply Active
            if active_info:
                agg_data[code]["is_disposed"] = True
                agg_data[code]["period"] = active_info["period"]
                agg_data[code]["measure"] = active_info["measure"]
            
            # Apply Future
            if future_info:
                agg_data[code]["future_period"] = future_info["period"]
                agg_data[code]["future_measure"] = future_info["measure"]
                # If NOT active (e.g. only future), we might want to flag it?
                # agg_data[code]["is_future_disposed"] = True # Optional

        
        # --- IMPORTANT: 從 listening_history.json 補充歷史條款 ---
        # 這樣快速更新和日期更新會有一致的行為
        if hasattr(self, 'history_manager') and self.history_manager:
            from datetime import timedelta
            import datetime as _dt_worker

            # 取得最近 45 天的資料（覆蓋 30 個交易日，供 Rule 4: 30日內12次 使用）
            lookback_days = 45
            start_date = last_trading_day - timedelta(days=lookback_days)
            current = start_date
            
            t_year = last_trading_day.year
            t_month = last_trading_day.month
            
            while current <= last_trading_day:
                records = self.history_manager.get_listening_data(current)
                
                if records:
                    for r in records:
                        code = r["code"]
                        # 過濾權證
                        if len(str(code)) == 5:
                            continue
                        
                        trigger_info = r.get("trigger_info", {})
                        if isinstance(trigger_info, str):
                            try:
                                trigger_info = json.loads(trigger_info)
                            except:
                                trigger_info = {}
                        
                        # [Fix] 如果股票不在 agg_data 中，必須將它加入，否則無法進行「一進聽」預測
                        if code not in agg_data:
                            # 加入基本的 agg_data 結構
                            agg_data[code] = {
                                "name": r.get("name", ""),
                                "source": r.get("source", "上市"),
                                "clauses": {},
                                "disposition": None,
                                "is_disposed": False,
                                "period": "",
                                "can_short": False, # 這邊預設 false，稍後需要可再查
                                "has_futures": r.get("has_futures", False)
                            }
                        
                        if trigger_info:
                            for date_key, clause_val in trigger_info.items():
                                # [Fix] 只填入 ≤ last_trading_day 的條款，避免未來日期污染預測
                                try:
                                    parts = date_key.split("/")
                                    if len(parts) == 2:
                                        m, d = int(parts[0]), int(parts[1])
                                        y = t_year
                                        if t_month == 1 and m == 12:
                                            y = t_year - 1
                                        elif t_month == 12 and m == 1:
                                            y = t_year + 1
                                        clause_dt = _dt_worker.datetime(y, m, d)
                                        if clause_dt.date() > last_trading_day.date():
                                            continue
                                except:
                                    pass  # 日期解析失敗時不過濾
                                
                                if date_key not in agg_data[code]["clauses"] or not agg_data[code]["clauses"][date_key]:
                                    agg_data[code]["clauses"][date_key] = clause_val
                
                current += timedelta(days=1)
            
            print(f"[HistoryWorker] 已從 listening_history 補充歷史條款（已過濾未來日期）")

            
        self.data_ready.emit(agg_data)


class Dashboard(QWidget):
    status_message_updated = pyqtSignal(str) 
    initial_load_finished = pyqtSignal() # Signal when first data load is done

    def __init__(self, history_manager=None, auto_start=True):
        super().__init__()
        self.history_manager = history_manager if history_manager else HistoryManager()
        
        # 搜尋功能相關變數
        self.search_cache = {}  # {stock_code_date: data}
        self.searched_stocks = set()  # 使用者搜尋新增的個股代碼
        
        self.agg_data = {} # Initialize agg_data
        
        self.init_ui()
        
        self.calc_cache = {} # code -> result_tuple
        
        # REMOVED: Auto GitHub sync and auto worker start
        # Users now control when to update via Quick Update button
            
    def init_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        
        self.main_scroll = QScrollArea()
        self.main_scroll.setWidgetResizable(True)
        self.main_scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        self.main_widget_content = QWidget()
        main_layout = QHBoxLayout(self.main_widget_content)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        self.main_scroll.setWidget(self.main_widget_content)
        outer_layout.addWidget(self.main_scroll)
        
        # LEFT SIDE
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0,0,0,0)
        
        # --- Navigation Bar (NEW) ---
        nav_layout = QHBoxLayout()
        
        # Style for Nav Buttons
        btn_style = """
            QPushButton {
                background-color: #333333;
                color: #FFFFFF;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #444444; }
        """
        
        # 搜尋區域（左側）
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("輸入股票代碼...")
        self.search_input.setFixedWidth(120)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #1E1E1E;
                color: #FFFFFF;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 5px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #4da6ff;
            }
        """)
        self.search_input.returnPressed.connect(self.search_stock)
        nav_layout.addWidget(self.search_input)
        
        self.btn_search = QPushButton("搜尋")
        self.btn_search.setFixedSize(60, 30)
        self.btn_search.setStyleSheet(btn_style)
        self.btn_search.clicked.connect(self.search_stock)
        nav_layout.addWidget(self.btn_search)
        
        self.btn_clear_search = QPushButton("清空")
        self.btn_clear_search.setFixedSize(60, 30)
        self.btn_clear_search.setStyleSheet(btn_style)
        self.btn_clear_search.clicked.connect(self.clear_search)
        nav_layout.addWidget(self.btn_clear_search)
        
        # [NEW] 下載GitHub聽牌 按鈕
        self.btn_github_sync = QPushButton("下載GitHub聽牌")
        self.btn_github_sync.setFixedSize(120, 30)
        self.btn_github_sync.setStyleSheet(btn_style) # Use same style
        self.btn_github_sync.setToolTip("從 GitHub 下載並覆蓋 [當前選擇日期] 的聽牌資料 (本地編輯將被覆蓋)")
        self.btn_github_sync.clicked.connect(self.on_github_sync_click)
        nav_layout.addWidget(self.btn_github_sync)
        
        # [NEW] 下載官方處置 按鈕
        self.btn_official_disposal = QPushButton("下載官方處置")
        self.btn_official_disposal.setFixedSize(120, 30)
        self.btn_official_disposal.setStyleSheet(btn_style)
        self.btn_official_disposal.setToolTip("從證交所/櫃買中心下載 [當前選擇日期] 的處置公告 (將更新到資料庫)")
        self.btn_official_disposal.clicked.connect(self.on_official_disposal_click)
        nav_layout.addWidget(self.btn_official_disposal)
        
        # [NEW] 下載注意條款 按鈕
        self.btn_clause_download = QPushButton("下載注意條款")
        self.btn_clause_download.setFixedSize(120, 30)
        self.btn_clause_download.setStyleSheet(btn_style)
        self.btn_clause_download.setToolTip("下載過去 9 個交易日的注意條款（第 1-8 款，4 碼股票）")
        self.btn_clause_download.clicked.connect(self.on_download_clauses_click)
        nav_layout.addWidget(self.btn_clause_download)
        
        # [NEW] 更新融券/期貨 按鈕
        self.btn_update_mf = QPushButton("更新融券/期貨")
        self.btn_update_mf.setFixedSize(120, 30)
        self.btn_update_mf.setStyleSheet("""
            QPushButton {
                background-color: #1A3A5C;
                color: #4da6ff;
                border: 1px solid #4da6ff;
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #224466; }
            QPushButton:pressed { background-color: #0E2233; }
        """)
        self.btn_update_mf.setToolTip("從台灣證交所/櫃買中心/期交所下載最新融券&期貨清單，存入永久 DB。下方表格與資訊區將使用此資料。")
        self.btn_update_mf.clicked.connect(self.on_update_margin_futures_click)
        nav_layout.addWidget(self.btn_update_mf)
        
        nav_layout.addStretch()
        
        # Prev Button
        self.btn_prev = QPushButton("<")
        self.btn_prev.setFixedSize(40, 40)
        self.btn_prev.setStyleSheet(btn_style)
        self.btn_prev.clicked.connect(lambda: self.change_date(-1))
        
        nav_layout.addWidget(self.btn_prev)
        
        # Date Button (Display)
        self.date_btn = QPushButton("載入中...")
        self.date_btn.setFixedSize(140, 40)
        self.date_btn.setStyleSheet("""
            QPushButton {
                background-color: #1E1E1E;
                color: #4da6ff;
                border: 1px solid #4da6ff;
                border-radius: 4px;
                font-weight: bold;
                font-size: 16px;
            }
        """)
        self.date_btn.clicked.connect(self.toggle_calendar)
        nav_layout.addWidget(self.date_btn)

        # [NEW] Info Button (?) - REMOVED (Moved to Toolbar)
        # self.info_btn = QPushButton("?")
        # ...
        
        # 重新下載此日期資料按鈕
        self.refresh_date_btn = QPushButton("🔄")
        self.refresh_date_btn.setFixedSize(40, 40)
        self.refresh_date_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF6B35;
                color: #FFFFFF;
                border: 1px solid #FF8C00;
                border-radius: 4px;
                font-weight: bold;
                font-size: 18px;
            }
            QPushButton:hover { background-color: #FF8C00; }
            QPushButton:pressed { background-color: #CC5529; }
        """)
        self.refresh_date_btn.setToolTip("重新下載此日期的完整資料（處置公告、融資融券等）")
        self.refresh_date_btn.clicked.connect(self.start_refresh_current_date)
        nav_layout.addWidget(self.refresh_date_btn)
        
        # Next Button
        self.btn_next = QPushButton(">")
        self.btn_next.setFixedSize(40, 40)
        self.btn_next.setStyleSheet(btn_style)
        self.btn_next.clicked.connect(lambda: self.change_date(1))
        nav_layout.addWidget(self.btn_next)
        
        nav_layout.addStretch()
        
        left_layout.addLayout(nav_layout)
        
        # ... (Rest of init_ui logic is skipped in replace_file_content if I don't select it?)
        # Wait, I cannot easily replace just __init__ without replacing huge chunks if I use a range.
        # But I can replace `class Dashboard` header and `__init__` specifically if I target lines.

        # Let's try Multi-Edit for precision.


        
        # Initial Date State
        self.current_display_date = DateUtils.get_last_trading_day()
        self.cache_manager = CacheManager()
        # self.history_manager = HistoryManager() # Moved to __init__
        self.calc_cache = {} # Init Calculation Cache
        
        # [NEW] 融券/期貨永久資料庫
        self.mf_db = MarginFuturesDatabase()
        
        # [NEW] 可轉債（CB）資料庫
        from core.cb_data import CBDatabase
        self.cb_db = CBDatabase()
        
        # TOP AREA
        top_area = QHBoxLayout()
        # [MOD] 新增「更新」按鈕到聽牌區 (ObserverBox)
        self.observer_box = InfoBox("觀察區", {}, "#4da6ff", header_btn_text="[更新]", header_btn_callback=self.update_listening_only)
        self.observer_box.item_clicked.connect(self.highlight_stock)
        top_area.addWidget(self.observer_box, 1)
        self.disposition_box = InfoBox("盤後處置公告", {}, "#FFCC00")
        self.disposition_box.item_clicked.connect(self.highlight_stock)
        top_area.addWidget(self.disposition_box, 1)

        # [NEW] 處置中
        self.disp_active_box = InfoBox("處置中", {}, "#FFA500")
        self.disp_active_box.item_clicked.connect(self.highlight_stock)
        top_area.addWidget(self.disp_active_box, 1)

        self.disp_exit_box = InfoBox("處置出關區", {}, "#FF4444")
        self.disp_exit_box.item_clicked.connect(self.highlight_stock)
        top_area.addWidget(self.disp_exit_box, 1)
        
        left_layout.addLayout(top_area, 1)
        
        # 搜尋結果顯示區域
        self.search_result_box = InfoBox("搜尋結果", {}, "#00FF00")
        self.search_result_box.item_clicked.connect(self.highlight_stock)
        self.search_result_box.setVisible(False)  # 預設隱藏
        left_layout.addWidget(self.search_result_box)
        
        # MIDDLE TABLE
        self.grid_table = QTableWidget()
        self.grid_table.verticalHeader().setVisible(False)
        self.grid_table.setAlternatingRowColors(False) # Disable auto-alternating
        self.grid_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.grid_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid_table.setSortingEnabled(False)
        self.grid_table.horizontalHeader().setSectionsClickable(True)
        self.grid_table.horizontalHeader().sectionClicked.connect(self.on_header_clicked)
        
        self._cur_sort_col = -1
        self._cur_sort_order = Qt.SortOrder.AscendingOrder
        
        self.update_headers()
        left_layout.addWidget(self.grid_table, 3)
        main_layout.addWidget(left_container, 4)
        
        # RIGHT PANEL
        right_panel = QFrame()
        right_panel.setStyleSheet("QFrame { border: 2px solid #4da6ff; border-radius: 4px; background-color: #1E1E1E; } QLabel { border: none; font-size: 14px; color: #CCC; }")
        right_layout = QVBoxLayout(right_panel)
        
        # Right Panel Layout
        self.right_heading = QLabel("進處置條件 (點擊股票):")
        self.right_heading.setStyleSheet("font-size: 20px; font-weight: bold; color: #FF4444; margin-bottom: 5px;")
        right_layout.addWidget(self.right_heading)
        
        # Use QTextEdit for better selection support
        self.condition_lbl = QTextEdit()
        self.condition_lbl.setReadOnly(True)
        self.condition_lbl.setObjectName("condition_lbl")
        self.condition_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.condition_lbl.setStyleSheet("QTextEdit { border: none; background-color: transparent; font-size: 16px; color: #DDDDDD; margin-top: 5px; font-weight: bold; }")
        right_layout.addWidget(self.condition_lbl, 1) # Expand
        
        # MOVED STATUS TO MAIN WINDOW STATUS BAR
        self.status_lbl = QLabel("") 
        self.status_lbl.setObjectName("status_lbl")
        right_layout.addWidget(self.status_lbl)
        
        # right_layout.addStretch() # Removed stretch so condition_lbl can expand
        main_layout.addWidget(right_panel, 1)
        
    def highlight_stock(self, code):
        """Scroll to row and Trigger Fetch."""
        if not code: return
        
        # 1. UI Selection
        found = False
        source = None
        for row in range(self.grid_table.rowCount()):
            item = self.grid_table.item(row, 0)
            if item and item.text().strip() == code.strip():
                self.grid_table.selectRow(row)
                self.grid_table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtTop)
                self.grid_table.setFocus()
                found = True
                
                # Get Source
                src_item = self.grid_table.item(row, 2)
                if src_item: source = src_item.text()
                break
        
        if found:
            # 2. Trigger Calculation
            self.right_heading.setText(f"進處置條件 ({code}):")
            
            # Check Cache
            if code in self.calc_cache:
                self.condition_lbl.setText("讀取快取中...")
                self.update_conditions((code, self.calc_cache[code]))
                return

            self.condition_lbl.setText("正在計算中...")
            
            # Start Worker
            # 獲取股票名稱 - 優先從 agg_data,若不存在則從表格
            stock_name = ""
            if hasattr(self, 'agg_data') and code in self.agg_data:
                stock_name = self.agg_data[code].get("name", "")
            else:
                # 從表格的 Name 欄位獲取
                for row in range(self.grid_table.rowCount()):
                    code_item = self.grid_table.item(row, 0)
                    if code_item and code_item.text().strip() == code:
                        name_item = self.grid_table.item(row, 1)
                        if name_item:
                            stock_name = name_item.text()
                        break
            
            self.calc_worker = CalculationWorker(code, source, stock_name=stock_name, target_date=self.current_display_date)
            self.calc_worker.result_ready.connect(self.update_conditions)
            self.calc_worker.start()
            
    def update_conditions(self, payload):
        """
        Payload: (code, result_tuple)
        result_tuple: (calc_lines, excl_lines)
        """
        if not payload:
            self.condition_lbl.setText("無資料或無需計算")
            return
            
        try:
            # Detect Payload Type (Cache call vs Signal call)
            # Signal emits (code, (lines, excl))
            # Cache call passes (lines, excl) or stored tuple
            
            target_code = None
            result_tuple = None
            
            if isinstance(payload, tuple) and len(payload) == 2 and isinstance(payload[0], str):
                # Signal: (code, result)
                target_code = payload[0]
                result_tuple = payload[1]
            else:
                # Direct Call (from Cache): result_tuple
                target_code = getattr(self, 'calc_worker', None).code if hasattr(self, 'calc_worker') else None
                result_tuple = payload
                
            if not result_tuple:
                self.condition_lbl.setText("無資料")
                return

            # Update Cache (If code is known)
            if target_code and result_tuple:
                self.calc_cache[target_code] = result_tuple

            # 檢查是否為當前點擊的股票 (解決從快取讀取時 active_code 未更新導致被忽略的 Bug)
            current_selected_code = None
            selected_items = self.grid_table.selectedItems()
            if selected_items:
                row = selected_items[0].row()
                code_item = self.grid_table.item(row, 0)
                if code_item:
                    current_selected_code = code_item.text().strip()

            active_code = getattr(self, 'calc_worker', None).code if hasattr(self, 'calc_worker') and getattr(self, 'calc_worker', None) else None
            
            # If we received a signal from an old worker, update cache but DO NOT update UI
            # 只有當 target_code 不是當前選取的股票時，才忽略 UI 更新
            if target_code and current_selected_code and target_code != current_selected_code:
                return

            # Unpack Result
            if isinstance(result_tuple, tuple):
                calc_lines, excl_lines = result_tuple
            else:
                calc_lines = result_tuple
                excl_lines = ["尚未設定"]

            # 1. Calculation Logic
            html_content = "<br>".join(calc_lines)
            
            # 2. Exclusion Logic
            html_content += "<br><br><div style='color: #FF4444; font-weight: bold; margin-bottom: 5px; font-size: 20px;'>排除條件:</div>"
            
            if excl_lines:
                 html_content += "<div style='color: #DDDDDD; font-size: 16px; font-weight: bold; line-height: 1.5;'>" + "<br>".join(excl_lines) + "</div>"
            else:
                 html_content += "<div style='color: #888888;'>無資料</div>"
            
            full_html = f"<html><body>{html_content}</body></html>"
            self.condition_lbl.setHtml(full_html)
            
        except Exception as e:
            print(f"Error updating conditions: {e}")
            self.condition_lbl.setText(f"資料更新錯誤: {e}")

# --- Worker ---
    def update_headers(self):
        last_trading_day = DateUtils.get_last_trading_day()
        self.update_headers_for_date(last_trading_day)

    def update_headers_for_date(self, target_date):
        # Regenerate calendar if not already done (though caller usually does)
        # Assuming self.calendar is up to date or we update it here
        self.calendar = DateUtils.get_market_calendar(target_date, past_days=9, future_days=9)

        headers = ["股票", "名稱", "類別", "處置頻率", "處置天數", "融券", "期貨", "CB"]
        headers.extend(self.calendar["past"]) # Hist days
        headers.append(f"{self.calendar['current']} (今)") # 1 day
        headers.extend(self.calendar["future"]) # Future days
        headers.append("機率") # Probability Column
        headers.append("處置預測") # Prediction Column (Last)
        headers.append("Original_ID") # Hidden for Reset

        self.grid_table.setColumnCount(len(headers))
        self.grid_table.setHorizontalHeaderLabels(headers)
        self.grid_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.grid_table.setColumnHidden(len(headers)-1, True)

        # Update Date Button Text
        y = target_date.year
        m = target_date.month
        d = target_date.day
        self.date_btn.setText(f"{y}/{m:02d}/{d:02d} ▼")

    def start_worker(self, force_refresh=False):
        # [Cache Check]
        if not force_refresh:
            try:
                 date_str = self.current_display_date.strftime("%Y%m%d")
                 cached_agg = self.cache_manager.get_agg_data(date_str)
                 if cached_agg:
                     print(f"DEBUG: Loaded data from Cache for {date_str}")
                     self.on_data_ready(cached_agg)
                     return
            except Exception as e:
                print(f"Cache Load Failed: {e}")

        self.status_message_updated.emit("正在下載歷史資料...") # Emit Signal
        self.worker = HistoryWorker([], history_manager=self.history_manager)
        self.worker.progress_update.connect(self.update_status)
        self.worker.data_ready.connect(self.on_data_ready)
        self.worker.start()

    def auto_refresh_listening_on_startup(self, on_complete=None):
        """
        [Fix 2026-09-01] 開軟體時一律自動補上聽牌(官方)最新資料，不受
        start_worker() 的 agg_cache 快取捷徑、也不受 HistoryWorker 的夜間
        7-11 點時段限制影響。呼叫端(main_window.py)完成後才繼續載入處置預測，
        確保 forecast_page 讀到的 history_manager 已經是當天最新的聽牌名單。
        """
        self._listening_refresh_on_complete = on_complete
        self._listening_fetch_worker = ListeningFetchWorker(self.current_display_date)
        self._listening_fetch_worker.fetched.connect(self._on_listening_fetched)
        self._listening_fetch_worker.start()

    def _on_listening_fetched(self, att_list):
        try:
            from datetime import datetime
            import json
            display_date = self.current_display_date.date()
            today = datetime.now().date()
            last_trading_day = DateUtils.get_last_trading_day().date()

            if att_list and (display_date == today or display_date == last_trading_day):
                target_dt = self.current_display_date
                count = 0
                for item in att_list:
                    code = str(item.get('code', '')).strip()
                    if not code:
                        continue
                    reason_str = item.get('reason', '')
                    date_key = target_dt.strftime("%m/%d")
                    parsed_clause = ClauseParser.parse_clauses(reason_str)

                    raw_source = item.get('source', '')
                    if raw_source in ("TWSE", "tse", "上市"):
                        market_src = "上市"
                    elif raw_source in ("TPEX", "otc", "OTC", "上櫃"):
                        market_src = "上櫃"
                    else:
                        market_src = "上市"

                    record = {
                        "date": target_dt.strftime("%Y-%m-%d"),
                        "code": code,
                        "name": item.get('name', ''),
                        "reason": reason_str,
                        "source": market_src,
                        "trigger_info": json.dumps({date_key: parsed_clause}, ensure_ascii=False) if parsed_clause else "{}",
                        "is_disposed_next_day": False,
                        "tags": [],
                        "comment": ""
                    }
                    self.history_manager.add_record(record)
                    count += 1
                print(f"[auto_refresh_listening] 已更新 {count} 筆聽牌資料 ({target_dt.strftime('%Y-%m-%d')})")

                try:
                    self.update_info_boxes(getattr(self, 'agg_data', {}))
                except Exception as e:
                    print(f"[auto_refresh_listening] update_info_boxes 失敗: {e}")
            else:
                print(f"[auto_refresh_listening] 無資料或非今日/最近交易日，略過寫入 (display_date={display_date})")
        except Exception as e:
            print(f"[auto_refresh_listening] Error: {e}")
        finally:
            callback = getattr(self, '_listening_refresh_on_complete', None)
            self._listening_refresh_on_complete = None
            if callback:
                try:
                    callback()
                except Exception as e:
                    print(f"[auto_refresh_listening] callback error: {e}")

    def auto_refresh_clauses_on_startup(self, on_complete=None):
        """
        [Fix 2026-09-03] attention_clauses 表(聽牌/一進聽計算的累積次數依據)原本
        只能靠「下載注意條款」按鈕手動觸發，實測發現這個表從 2026-08-20 之後整整
        兩週沒有新資料，導致 min_needed 嚴重低估——本來只差最後一次注意就會進處置
        的股票(官方聽牌名單裡的)，被本地算成還差兩次，才會出現「聽牌股同時也在
        一進聽」這種看起來矛盾的畫面。

        跟 auto_refresh_listening_on_startup 同一套模式：開軟體時自動補齊，不用
        使用者手動按按鈕才會更新。自動偵測 attention_clauses 目前最新的公告日，
        只回補「最新公告日+1」到「今天」這段真正缺的範圍(避免每次開軟體都重抓
        整整 8-9 個交易日，多數時候應該只差 0-1 天)；表是空的或抓不到最新日期時，
        才退回跟手動按鈕一樣的預設範圍(往前 8 個交易日)。
        """
        self._clauses_refresh_on_complete = on_complete
        self._clauses_fetch_worker = ClausesGapFetchWorker(self.current_display_date)
        self._clauses_fetch_worker.finished_ok.connect(self._on_clauses_refreshed)
        self._clauses_fetch_worker.start()

    def _on_clauses_refreshed(self, saved_count, error_msg):
        if error_msg:
            print(f"[auto_refresh_clauses] Error: {error_msg}")
        else:
            print(f"[auto_refresh_clauses] 已補齊 {saved_count} 筆注意條款")
            if saved_count > 0:
                # 補進去的新資料，agg_data 裡的 clauses 是舊快取，重新合併一次
                # 再刷新觀察區，讓聽牌/一進聽的累積次數用最新資料算，不用等
                # 使用者手動切換日期才會重新讀。
                try:
                    agg_data = getattr(self, 'agg_data', {})
                    if agg_data:
                        self._merge_clauses_from_db(agg_data, self.current_display_date)
                        self._merge_clauses_from_listening_history(agg_data, self.current_display_date)
                        self.update_info_boxes(agg_data)
                except Exception as e:
                    print(f"[auto_refresh_clauses] update_info_boxes 失敗: {e}")
        callback = getattr(self, '_clauses_refresh_on_complete', None)
        self._clauses_refresh_on_complete = None
        if callback:
            try:
                callback()
            except Exception as e:
                print(f"[auto_refresh_clauses] callback error: {e}")

    def start_quick_update(self):
        """
        Called by MainWindow Quick Update button.
        MODIFIED: Now fetches today's listening data from APIs and updates history.
        Note: This is temporary - nightly GitHub CI will overwrite with official data.
        
        IMPORTANT: 允許在當天 13:30 前更新昨天的盤後資料
        因為盤後資料通常在收盤後才公布，而隔天開盤前(13:30)這段時間
        應該還是可以更新昨天的盤後資料
        """
        from datetime import datetime, time, timedelta
        
        # 取得當前時間
        now = datetime.now()
        today = now.date()
        display_date = self.current_display_date.date()
        
        # 判斷是否可以更新
        # 1. 如果選擇的是今天，可以更新
        # 2. 如果選擇的是昨天，且當前時間 < 13:30，可以更新（盤後資料）
        can_update = False
        
        if display_date == today:
            can_update = True
        elif display_date == today - timedelta(days=1):
            # 昨天的資料，檢查是否在 13:30 前
            if now.time() < time(13, 30):
                can_update = True
        
        if not can_update:
            print(f"[Quick Update] Skipped - cannot update {display_date}")
            print(f"[Quick Update] Current time: {now.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"[Quick Update] Can update today or yesterday (before 13:30)")
            
            # Show a message to user
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "無法更新",
                f"快速更新限制：\n\n"
                f"• 可更新今天的資料\n"
                f"• 或在開盤前(13:30)更新昨天的盤後資料\n\n"
                f"當前選擇：{display_date}\n"
                f"今天：{today}\n"
                f"當前時間：{now.strftime('%H:%M:%S')}"
            )
            return
        
        # 1. Fetch today's listening data from APIs
        try:
            today_dt = datetime.now()
            today_str = today_dt.strftime("%Y%m%d")
            
            # [NEW] Fetch & Update Disposition Data (for 8046 announcement)
            try:
                print(f"[Quick Update] Fetching Disposition Data for {today_str}...")
                from core.fetcher import StockFetcher
                from core.parser import StockParser
                from core.disposal_database import DisposalDatabase
                
                fetcher = StockFetcher()
                parser = StockParser()
                db = DisposalDatabase()
                
                # TWSE
                twse_data = fetcher.fetch_twse_disposition(today_str)
                if twse_data:
                    parsed = parser.parse_twse_disposition(twse_data)
                    count = 0
                    cursor = db.conn.cursor()
                    for item in parsed:
                        # Insert logic similar to db.import_from_csv but direct
                        # Simpler: just use SQL insert
                        try:
                            start, end = db.parse_period(item.get("period"))
                            cursor.execute("""
                                INSERT OR REPLACE INTO disposal_records
                                (source, announce_date, code, name, period_start, period_end, 
                                 period_raw, measure, reason, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                            """, (
                                "上市",
                                item.get("date"), # Announce Date
                                item.get("code"),
                                item.get("name"),
                                start, end,
                                item.get("period"),
                                item.get("measure"),
                                item.get("reason")
                            ))
                            count += 1
                        except:
                            pass
                    db.conn.commit()
                    print(f"[Quick Update] Updated {count} TWSE Dispositions.")
            except Exception as e:
                print(f"[Quick Update] Error updating dispositions: {e}")

            att_list = AttentionScraper.fetch_data(today_dt)
            
            if att_list:
                print(f"[Quick Update] Fetched {len(att_list)} listening stocks for today.")
                
                import json
                
                # 2. Update to history_manager
                for item in att_list:
                    reason_str = item.get('reason', '')
                    date_key = today_dt.strftime("%m/%d")
                    parsed_clause = ClauseParser.parse_clauses(reason_str)
                    
                    record = {
                        "date": today_dt.strftime("%Y-%m-%d"),
                        "code": item['code'],
                        "name": item['name'],
                        "reason": reason_str,
                        "source": item.get('source', '上市'),
                        "trigger_info": json.dumps({date_key: parsed_clause}, ensure_ascii=False) if parsed_clause else "{}",
                        "is_disposed_next_day": False,
                        "tags": [],
                        "comment": ""
                    }
                    self.history_manager.add_record(record)
                
                print("[Quick Update] Today's listening data updated to history.")
            else:
                print("[Quick Update] No listening data found for today.")
        except Exception as e:
            print(f"[Quick Update] Error fetching listening data: {e}")
        
        # 3. Execute normal refresh (margin lists, futures, etc.)
        self.start_worker(force_refresh=True)

    def update_listening_only(self):
        """
        [NEW] 單純更新聽牌股資料 (不觸發全量計算)
        應使用者要求：只更新聽牌區(官方)，不作其他更動
        """
        from datetime import datetime
        from core.scraper_attention import AttentionScraper
        from PyQt6.QtWidgets import QMessageBox

        # 這裡不限制時間，因為用戶明確按下按鈕
        # 但如果是查詢歷史日期，可能抓不到當天的
        display_date = self.current_display_date.date()
        today = datetime.now().date()
        
        if display_date != today:
             # 簡單提醒，還是允許執行 (也許官方有補歷史資料?)
             # 但 User 要求只更新官方聽牌，TWSE 支持歷史，TPEX 總是最新
             pass

        self.update_status("正在更新聽牌資料...")
        try:
            # 調用已修正的爬蟲
            # 注意：TPEX 總是抓最新，TWSE 如果傳入日期會抓那天
            # 為了符合 UI 當前日期，我們傳入 current_display_date
            target_dt = self.current_display_date
            
            att_list = AttentionScraper.fetch_data(target_dt)
            
            if att_list:
                print(f"[Update Listening] Fetched {len(att_list)} stocks.")
                
                # 寫入歷史紀錄 (僅限今天或最近一個交易日)
                # 因為爬蟲 TWSE/TPEX 預設只抓取最新資料
                # 若允許歷史日期寫入，會導致今天的聽牌股被錯誤寫入過去日期，污染歷史紀錄！
                from core.utils import DateUtils
                last_trading_day = DateUtils.get_last_trading_day().date()
                
                import json
                if display_date == today or display_date == last_trading_day:
                    for item in att_list:
                        reason_str = item.get('reason', '')
                        date_key = target_dt.strftime("%m/%d")
                        parsed_clause = ClauseParser.parse_clauses(reason_str)
                        
                        record = {
                            "date": target_dt.strftime("%Y-%m-%d"),
                            "code": item['code'],
                            "name": item['name'],
                            "reason": reason_str,
                            "source": item.get('source', '上市'),
                            "trigger_info": json.dumps({date_key: parsed_clause}, ensure_ascii=False) if parsed_clause else "{}", 
                            "is_disposed_next_day": False,
                            "tags": [],
                            "comment": ""
                        }
                        self.history_manager.add_record(record)
                    print("[Update Listening] Today's or recent listening data updated to history.")
                else:
                    print(f"[Update Listening] Warning: target date {display_date} is not today or latest trading day. Showing fetched data in UI but skipping saving to history to prevent data pollution.")

                self.update_status("聽牌資料更新完成")
                QMessageBox.information(self, "更新完成", f"已更新 {len(att_list)} 筆聽牌資料。")
                
                # [效能優化] 只刷新聽牌區顯示，不重跑整個 update_info_boxes
                # update_info_boxes 會對 283 筆 agg_data 做同步網路請求，極度耗時
                try:
                    self.update_info_boxes(getattr(self, 'agg_data', {}))
                except Exception as e:
                    print(f"[Update Listening] Refresh Error: {e}")
            else:
                self.update_status("查無聽牌資料")
                QMessageBox.information(self, "更新完成", "官方 API 返回 0 筆資料。")
                
        except Exception as e:
            print(f"[Update Listening] Error: {e}")
            self.update_status("更新失敗")
            QMessageBox.warning(self, "更新失敗", str(e))
    
    def start_refresh_current_date(self):
        """
        重新下載當前選擇日期的完整資料
        
        觸發 HistoricalDataRefreshWorker 重新獲取：
        - 處置公告（含處置頻率）
        - 融資融券清單
        - 注意股資料（從 listening_history.json）
        """
        from PyQt6.QtWidgets import QMessageBox
        
        # 確認操作
        display_date_str = self.current_display_date.strftime("%Y/%m/%d")
        reply = QMessageBox.question(
            self,
            "確認重新下載",
            f"確定要重新下載 {display_date_str} 的完整資料嗎？\n\n"
            f"這將：\n"
            f"• 從證交所/櫃買中心下載最新資料\n"
            f"• 覆蓋現有快取\n"
            f"• 可能需要 10-30 秒",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # 禁用按鈕避免重複點擊
        self.refresh_date_btn.setEnabled(False)
        self.refresh_date_btn.setText("⏳")
        
        # 建立並啟動 Worker
        self.refresh_worker = HistoricalDataRefreshWorker(
            target_date=self.current_display_date,
            lookback_days=14
        )
        
        # 連接信號
        self.refresh_worker.progress_update.connect(self.on_refresh_progress)
        self.refresh_worker.data_ready.connect(self.on_refresh_complete)
        self.refresh_worker.error.connect(self.on_refresh_error)
        
        # 啟動
        self.refresh_worker.start()
    
    def on_refresh_progress(self, message):
        """處理重新下載的進度訊息"""
        print(f"[Refresh] {message}")
    
    def on_refresh_complete(self, agg_data, date_str):
        """處理重新下載完成"""
        from PyQt6.QtWidgets import QMessageBox
        
        # 保存數據以備不時之需 (雖然 load_data_for_date 會再次觸發)
        self.agg_data = agg_data
        
        print(f"[Refresh] Complete! Got {len(agg_data)} stocks for {date_str}")
        
        # 恢復按鈕
        self.refresh_date_btn.setEnabled(True)
        self.refresh_date_btn.setText("🔄")
        
        # 顯示完成訊息
        QMessageBox.information(
            self,
            "下載完成",
            f"成功下載並更新 {self.current_display_date.strftime('%Y/%m/%d')} 的資料！\n\n"
            f"共處理 {len(agg_data)} 支股票"
        )
        
        # 重新載入 Dashboard
        self.load_data_for_date(self.current_display_date)
    
    def on_refresh_error(self, error_message):
        """處理重新下載錯誤"""
        from PyQt6.QtWidgets import QMessageBox
        
        print(f"[Refresh] Error: {error_message}")
        
        # 恢復按鈕
        self.refresh_date_btn.setEnabled(True)
        self.refresh_date_btn.setText("🔄")
        
        # 顯示錯誤訊息
        QMessageBox.critical(
            self,
            "下載失敗",
            f"重新下載資料時發生錯誤：\n\n{error_message}"
        )
        
    def update_status(self, msg):
        # Emit signal instead of setting local label
        self.status_message_updated.emit(msg)
        
    def on_data_ready(self, agg_data):
        self.agg_data = agg_data # Save for later use (e.g. quick update)
        
        # [Fix] 從 attention_clauses DB 補充官方條款（一至八款）
        self._merge_clauses_from_db(agg_data, self.current_display_date)
        
        # [Fix] 從 listening_history.json 補充歷史條款（尤其是累積注意股）
        self._merge_clauses_from_listening_history(agg_data, self.current_display_date)

        # [Fix 2026-09-01] on_data_ready() 是開軟體時 start_worker() 的主要路徑
        # （不管是 cache-hit 還是 HistoryWorker 跑完都會走到這），之前
        # _merge_disposal_status_from_db() 只接在 load_data_for_date()（手動切換
        # 日期）裡，導致「盤後處置公告」等仰賴 is_disposed/future_period 的區塊
        # 在開軟體當下讀到的還是 agg_cache 建立當時的舊資料，即使 disposal_records
        # 早就有當天新公告的處置股，也要等使用者手動切一次日期才會刷新。這裡也補上
        # 同一個合併呼叫，確保開軟體當下就是以資料庫為準。
        self._merge_disposal_status_from_db(agg_data, self.current_display_date)

        # 1. Update Info Boxes (Fetches Scraper Data needed for Table too)
        self.update_info_boxes(agg_data)
        
        # [Fix] 無論是否呼叫 populate_table，都儲存完整 agg_data 到 cache
        # 舊邏輯只在 populate_table 末尾儲存，但 populate_table 只在最新交易日執行
        # 所以歷史日期（如 3/6）瀏覽後 cache 只存了不完整的 80 個股票而非 215 個
        try:
            date_str = self.current_display_date.strftime('%Y%m%d')
            self.cache_manager.save_agg_data(date_str, agg_data)
        except Exception as e:
            print(f"[Cache] Failed to save agg_data: {e}")
        
        # 2. Update Table (Only if we are actually at the context for the table, usually Today on init)
        # But start_worker fetches data for current_display_date.
        # If current_display_date is Today, we populate table.
        if DateUtils.is_trading_day(self.current_display_date) and self.current_display_date.date() == DateUtils.get_last_trading_day().date():
             self.populate_table(agg_data)
        elif self.current_display_date.date() == DateUtils.get_last_trading_day().date():
             # Even if not trading day (weekend), if it's "Latest", we populate table
             self.populate_table(agg_data)
             
        self.initial_load_finished.emit() 

    def update_info_boxes(self, agg_data):
        """Refactored Info Box Update Logic"""
        # Generate Local Calendar corresponding to the DISPLAY date
        local_calendar = DateUtils.get_market_calendar(self.current_display_date, past_days=8, future_days=9)
        anchor_year = local_calendar["anchor_obj"].year
        anchor_month = local_calendar["anchor_obj"].month
        today_dt = dt.datetime(anchor_year, anchor_month, local_calendar["anchor_obj"].day)
        
        # [Crawler Integration] Fetch Official Attention List
        # MODIFIED: Only use History Manager (GitHub synced data)
        # Removed AttentionScraper fallback - users control updates via Quick Update button
        try:
             t_date = self.current_display_date
             
             # Only use History Manager (no fallback to live scraping)
             history_records = []
             if self.history_manager:
                 history_records = self.history_manager.get_listening_data(t_date)
                 
             if history_records:
                 # Map History Records to Scraper Format
                 att_list = []
                 for r in history_records:
                     code = str(r["code"])
                     
                     # FILTER: Skip Warrants (5~6 digits) and invalid float codes (e.g. 3354.0)
                     if len(code) > 4 or "." in code:
                         continue
                     
                     reason_txt = r.get("reason", "")
                     if not reason_txt and r.get("trigger_info"):
                         # Try to parse trigger info for reason
                         reason_txt = "詳細請見聽牌紀錄" 
                         
                     # Source detection - Read from history record
                     source = r.get("source")
                     
                     # [Fix] 從 agg_data 取得正確的 source（已在 Worker 中確認）
                     if agg_data and code in agg_data and agg_data[code].get("source"):
                         source = agg_data[code]["source"]
                         
                     # 防禦性補正：若 source 依然為空或 None，動態識別以防 UI 遺漏
                     if not source or source == "None":
                         # [效能優化] 完全移除同步網路請求 fallback
                         # 原本會在 UI 主執行緒逐筆呼叫 StockFetcher().check_market_type()
                         # 每筆 ~500ms，20 筆就要 10 秒以上，導致聽牌更新極慢
                         # 改為信任 history record 或 agg_data 的 source 值，若無則預設為上市
                         source = "上市"

                     att_list.append({
                         "code": code,
                         "name": r["name"],
                         "reason": reason_txt,
                         "source": source
                     })
                 self.today_attention_list = att_list
             else:
                 # No history data - display empty
                 self.today_attention_list = []

             self.today_attention_map = {item['code']: item['reason'] for item in self.today_attention_list}
             self.today_attention_names = {item['code']: item['name'] for item in self.today_attention_list}
        except Exception as e:
             print(f"Attention Data Load Error: {e}")
             self.today_attention_list = []
             self.today_attention_map = {}
             self.today_attention_names = {}
             
        # Lists
        listening_twse = []
        listening_tpex = []
        one_step_twse = []
        one_step_tpex = []
        notice_twse = []
        notice_tpex = []
        active_data = {0: {"twse":[], "tpex":[]}, 1: {"twse":[], "tpex":[]}, 2: {"twse":[], "tpex":[]}}
        exit_data = {0: {"twse":[], "tpex":[]}, 1: {"twse":[], "tpex":[]}, 2: {"twse":[], "tpex":[]}, 3: {"twse":[], "tpex":[]}, 4: {"twse":[], "tpex":[]}}
        
        # 歷史日期 (擴展到 30 天以支持 Rule 4: 30日內12次)
        pred_history_dates = []
        curr = self.current_display_date
        count = 0
        while count < 30:
             if DateUtils.is_trading_day(curr):
                  pred_history_dates.insert(0, curr.strftime("%m/%d"))
                  count += 1
             curr = curr - dt.timedelta(days=1)
             
        sorted_codes = sorted(list(agg_data.keys()), key=str)

        # [Fix 2026-08-20] 一進聽(觀察區)計算需要知道「過去30個交易日內」是否有處置紀錄，
        # 才能讓下面 predictor.py 的 cutoff_idx 邏輯正確歸零重算(進入處置後，累積次數重新
        # 起算)——沒有這段的話，處置前的舊注意次數會一直被算進去，導致跟總覽頁
        # (forecast_page.py，那邊有做這個處置紀錄查詢)算出不同的「一進聽」名單。
        disp_map = {}
        try:
            from core.disposal_database import DisposalDatabase
            _disp_db = DisposalDatabase()
            for r in _disp_db.get_all_records():
                disp_map.setdefault(str(r['code']), []).append(r)
            _disp_db.close()
        except Exception as e:
            print(f"[Observer Box] disposal_records 讀取失敗: {e}")

        for code in sorted_codes:
            data = agg_data[code]
            name = data["name"]
            
            if "購" in name or "售" in name: continue
            if "DR" in name: continue
            
            # [Fix] Filter out warrants (5~6 digits) and invalid float codes (e.g. 3354.0)
            if "." in str(code): continue
            if len(str(code)) > 4: continue
            
            period = data.get("period", "")
            disp_start_dt = DateUtils.parse_period_start(period)
            disp_end_dt = DateUtils.parse_period_end(period)
            
            # 取得未來三天
            future_dts = []
            for d_str in local_calendar["future"]:
                 try:
                     dm = d_str.split("/")
                     m, d = int(dm[0]), int(dm[1])
                     y = anchor_year
                     if anchor_month == 12 and m == 1: y += 1
                     elif anchor_month == 1 and m == 12: y -= 1
                     future_dts.append(dt.datetime(y, m, d))
                 except:
                     future_dts.append(None)
            
            # --- Observer/One Step Logic ---
            if not data.get("is_disposed", False) and not disp_start_dt:
                # Calc Prediction for One-Step using clause analysis
                hist_items = []
                clauses_map = data.get("clauses", {})

                # [Fix 2026-08-20] 找出過去30個交易日內、已在當前顯示日(含)以前結束的處置期間，
                # 標記對應日期 is_disposed=True，讓 predictor.py 的 cutoff_idx 正確截斷處置前的
                # 舊紀錄，與 forecast_page.py 算法一致。
                disposal_periods = []
                for r in disp_map.get(str(code), []):
                    ps_str = r.get('period_start')
                    pe_str = r.get('period_end')
                    if ps_str and pe_str:
                        try:
                            ps_dt = datetime.strptime(ps_str, "%Y-%m-%d").date()
                            pe_dt = datetime.strptime(pe_str, "%Y-%m-%d").date()
                            if ps_dt <= self.current_display_date.date():
                                disposal_periods.append((ps_dt, pe_dt))
                        except:
                            pass

                # [Fix] 支援完整條款以供 30日12次 預測正確執行
                for d in pred_history_dates:
                     c_str = clauses_map.get(d, "")
                     valid_any_list = [c for c in c_str.split(',') if c.strip() in ['一', '二', '三', '四', '五', '六', '七', '八']]
                     is_any = len(valid_any_list) > 0
                     is_any_all = len(c_str.strip()) > 0

                     is_disp_day = False
                     try:
                         dm = d.split("/")
                         d_month, d_day = int(dm[0]), int(dm[1])
                         eff_year = anchor_year
                         if anchor_month == 1 and d_month == 12: eff_year -= 1
                         elif anchor_month == 12 and d_month == 1: eff_year += 1
                         d_date = dt.date(eff_year, d_month, d_day)
                         for ps, pe in disposal_periods:
                             if ps <= d_date <= pe:
                                 is_disp_day = True
                                 break
                     except:
                         pass

                     hist_items.append({"is_clause1": "一" in c_str, "is_any": is_any, "is_any_all": is_any_all, "is_disposed": is_disp_day})

                warning_msg, prob, min_needed = DispositionPredictor.analyze(hist_items, future_days=5)

                # Check One Step Away (min_needed == 2)
                # Note: min_needed == 1 means already "listening" (handled by history_manager)
                # min_needed == 2 means "one step away from listening"
                if min_needed == 2:
                     suffix = ""
                     if data.get("has_futures") or self.mf_db.has_futures(code): suffix += "(期)"
                     if self.cb_db.has_cb_now(code): suffix += "(CB)"
                     link = f"<a href='{code}' style='color: #E0E0E0; text-decoration: none;'>{code}&nbsp;{name}{suffix}</a>"
                     src_display = data["source"]
                     if src_display in ("TWSE", "tse"): src_display = "上市"
                     elif src_display in ("TPEX", "otc", "OTC", "TPEX ", " TPEX"): src_display = "上櫃"
                     if src_display == "上市": one_step_twse.append(link)
                     else: one_step_tpex.append(link)
            
            # --- Notice Box Logic (Future Disposal) ---
            # 盤後處置公告 = 當天公告、但尚未生效的處置
            # Check 1: Existing Logic (is_disposed w/ Future Start)
            is_future_start = False
            if data.get("is_disposed", False) and disp_start_dt:
                 display_date = self.current_display_date
                 # [Fix] 只要生效日 > 當前顯示日期即視為「盤後公告」
                 # 不再嚴格要求 == 下一個交易日，避免過年假期跨日造成失效
                 import datetime as _dt
                 display_dt_only = _dt.date(display_date.year, display_date.month, display_date.day)
                 if disp_start_dt.date() > display_dt_only:
                     is_future_start = True
            
            # Check 2: Concurrent Future Logic (New field)
            future_period = data.get("future_period", "")
            if future_period:
                f_start = DateUtils.parse_period_start(future_period)
                display_date = self.current_display_date
                # [Fix] 只要生效日 > 當前顯示日期即視為「盤後公告」
                # 不再嚴格要求 == 下一個交易日，避免過年假期跨日造成失效
                import datetime as _dt
                display_dt_only = _dt.date(display_date.year, display_date.month, display_date.day)
                if f_start and f_start.date() > display_dt_only:
                    is_future_start = True

            if is_future_start:
                 suffix = ""
                 if data.get("has_futures") or (hasattr(self, 'mf_db') and self.mf_db and self.mf_db.has_futures(code)): suffix += "(期)"
                 if hasattr(self, 'cb_db') and self.cb_db and self.cb_db.has_cb_now(code): suffix += "(CB)"
                 item_str = f"<a href='{code}' style='color: #E0E0E0; text-decoration: none;'>{code}&nbsp;{name}{suffix}</a>"
                 src_notice = data.get("source", "")
                 if src_notice in ("TWSE", "tse", "上市"): src_notice = "上市"
                 elif src_notice in ("TPEX", "otc", "OTC", "上櫃", " TPEX"): src_notice = "上櫃"
                 if src_notice == "上市": notice_twse.append(item_str)
                 else: notice_tpex.append(item_str)

            # --- Active Box Logic (處置中) ---
            # 新邏輯：只看「明天」(future_dts[0]) 是這檔股票處置的「第幾天」
            # 若是第 2 天 -> 放進 active_data[0]
            # 若是第 3 天 -> 放進 active_data[1]
            # 若是第 4 天 -> 放進 active_data[2]
            # [Fix] 如果有新的盤後處置公告 (is_future_start)，代表舊的處置已被覆蓋或延後，不應顯示在原本的處置進度與出關區
            if data.get("is_disposed", False) and not is_future_start and disp_start_dt and disp_end_dt and len(future_dts) > 0:
                tmr_dt = future_dts[0]
                if tmr_dt and disp_start_dt.date() <= tmr_dt.date() <= disp_end_dt.date():
                    # 計算明天是處置的第幾個「交易日」
                    disp_day_count = 0
                    curr_count_dt = disp_start_dt
                    while curr_count_dt.date() <= tmr_dt.date():
                        if DateUtils.is_trading_day(curr_count_dt):
                            disp_day_count += 1
                        curr_count_dt += dt.timedelta(days=1)
                        
                    # 判斷是否為第2, 3, 4天
                    target_idx = -1
                    if disp_day_count == 2: target_idx = 0
                    elif disp_day_count == 3: target_idx = 1
                    elif disp_day_count == 4: target_idx = 2
                    
                    if target_idx != -1:
                        suffix = ""
                        if data.get("has_futures") or self.mf_db.has_futures(code): suffix += "(期)"
                        if self.cb_db.has_cb_now(code): suffix += "(CB)"
                        item_str = f"<a href='{code}' style='color: #E0E0E0; text-decoration: none;'>{code}&nbsp;{name}{suffix}</a>"
                        
                        src_active = data["source"]
                        if src_active in ("TWSE", "tse"): src_active = "上市"
                        elif src_active in ("TPEX", "otc", "OTC"): src_active = "上櫃"
                        
                        if src_active == "上市":
                            active_data[target_idx]["twse"].append(item_str)
                        else:
                            active_data[target_idx]["tpex"].append(item_str)

            # --- Exit Box Logic ---
            if data.get("is_disposed", False) and not is_future_start and disp_end_dt:
                 # Future Dates from Local Calendar
                 future_dts = []
                 for d_str in local_calendar["future"]:
                      try:
                          dm = d_str.split("/")
                          m, d = int(dm[0]), int(dm[1])
                          y = anchor_year
                          if anchor_month == 12 and m == 1: y += 1
                          elif anchor_month == 1 and m == 12: y -= 1
                          future_dts.append(dt.datetime(y, m, d))
                      except:
                          future_dts.append(None)
                 
                 target_idx = -1
                 if disp_end_dt.date() == today_dt.date(): target_idx = 0
                 elif len(future_dts)>0 and future_dts[0] and disp_end_dt.date() == future_dts[0].date(): target_idx = 1
                 elif len(future_dts)>1 and future_dts[1] and disp_end_dt.date() == future_dts[1].date(): target_idx = 2
                 elif len(future_dts)>2 and future_dts[2] and disp_end_dt.date() == future_dts[2].date(): target_idx = 3
                 elif len(future_dts)>3 and future_dts[3] and disp_end_dt.date() == future_dts[3].date(): target_idx = 4  # Added for 5-day advance notice
                 
                 # [DEBUG] Print exit logic
                 if target_idx != -1:
                     print(f"[Exit] {code} {name} - End: {disp_end_dt.date()} | Today: {today_dt.date()} | target_idx: {target_idx}")
                 
                 if target_idx != -1 and target_idx in exit_data:
                     suffix = ""
                     if data.get("has_futures"): suffix += "(期)"
                     if self.cb_db.has_cb_now(code): suffix += "(CB)"
                     item_str = f"<a href='{code}' style='color: #E0E0E0; text-decoration: none;'>{code}&nbsp;{name}{suffix}</a>"
                     if data["source"] == "上市": exit_data[target_idx]["twse"].append(item_str)
                     else: exit_data[target_idx]["tpex"].append(item_str)
            elif data.get("is_disposed", False):
                # [DEBUG] No end date
                print(f"[Exit Debug] {code} {name} - is_disposed=True but disp_end_dt=None")

        # Fill Official Listening (From Scraper)
        listening_codes_set = set()
        for item in self.today_attention_list:
            c = item['code']
            n = item['name']
            src = item.get('source', '')
            # 正規化 source 字串
            if src in ("TWSE", "tse"): src = "上市"
            elif src in ("TPEX", "otc", "OTC"): src = "上櫃"
            has_futures_flag = (c in agg_data and agg_data[c].get('has_futures')) or self.mf_db.has_futures(c)

            suffix = ""
            if has_futures_flag: suffix += "(期)"
            if self.cb_db.has_cb_now(c): suffix += "(CB)"
            display_name = f"{n}{suffix}" if suffix else n
            link = f"<a href='{c}' style='color: #E0E0E0; text-decoration: none;'>{c}&nbsp;{display_name}</a>"

            if src == "上市": listening_twse.append(link)
            elif src == "上櫃": listening_tpex.append(link)
            # else: Source is "上市(條款)" or "上櫃(條款)" -> Skip Blue Box (Table Only)
            listening_codes_set.add(c)
        
        # [Fix 2026-09-03 撤回] 這裡之前一度拿掉了「聽牌股不重複列入一進聽」的過濾器、
        # 並把框名改成「今日注意(官方)」，理由是誤判 today_attention_list 只是「單日
        # 觸發」而非真聽牌。後來查明：today_attention_list 的資料來源(TWSE
        # notetrans + TPEx bulletin/warning)本來就是官方「累積注意次數已達/接近處置
        # 標準」的正式聽牌清單，用詞本身沒錯。真正的根因是 attention_clauses 這張表
        # 從 2026-08-20 之後到現在完全沒有新資料(下載這張表的 ClauseDownloadWorker
        # 只能手動按鈕觸發，沒有自動更新)，導致本地累積次數嚴重低估，算出來的
        # min_needed 才會把「其實只差最後一次(=1)」的股票誤判成「還差兩次(=2)」，
        # 才會出現同一檔股票「看起來」同時聽牌又一進聽的假象。已回補
        # attention_clauses 缺的資料、並把 ClauseDownloadWorker 併入開軟體自動更新
        # 流程(見 auto_refresh_clauses_on_startup)。這裡改回聽牌與一進聽互斥、
        # 框名改回「聽牌(官方)」，資料正確後兩者本來就不該重疊。
        one_step_twse = [link for link in one_step_twse if not any(f"href='{c}'" in link for c in listening_codes_set)]
        one_step_tpex = [link for link in one_step_tpex if not any(f"href='{c}'" in link for c in listening_codes_set)]

        # Update UI Boxes
        if not DateUtils.is_trading_day(self.current_display_date):
             # Clear logic on non-trading days
             obs_list = [("聽牌 (官方)", ""), ("     上市", ""), ("     上櫃", ""), ("一進聽 (預測)", ""), ("     上市", ""), ("     上櫃", "")]
             notice_list = [("上市", ""), ("     ", ""), ("上櫃", ""), ("     ", "")]
             # Exit list empty (handled below)
             exit_data = {0: {"twse":[], "tpex":[]}, 1: {"twse":[], "tpex":[]}, 2: {"twse":[], "tpex":[]}, 3: {"twse":[], "tpex":[]}, 4: {"twse":[], "tpex":[]}}  # Added index 4 for 5-day notice
             active_list = []
        else:
             obs_list = [
                ("聽牌 (官方)", ""),
                ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(listening_twse) if listening_twse else "無"),
                ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(listening_tpex) if listening_tpex else "無"),
                ("一進聽 (預測)", ""),
                ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(one_step_twse) if one_step_twse else "無"),
                ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(one_step_tpex) if one_step_tpex else "無")
             ]
             notice_list = [
                ("上市", ""),
                ("     ", "&nbsp;&nbsp;&nbsp;&nbsp;".join(notice_twse) if notice_twse else "無"),
                ("上櫃", ""),
                ("     ", "&nbsp;&nbsp;&nbsp;&nbsp;".join(notice_tpex) if notice_tpex else "無")
             ]

        self.observer_box.update_items(obs_list)
        self.disposition_box.update_items(notice_list)
        
        # Active Box Labels (處置中)
        def safe_date(idx):
             if idx < len(local_calendar["future"]): return local_calendar["future"][idx]
             return "??"
             
        active_lbl_1 = f"明天({safe_date(0)}) 處置第二天"
        active_lbl_2 = f"明天({safe_date(0)}) 處置第三天"
        active_lbl_3 = f"明天({safe_date(0)}) 處置第四天"
        
        active_list = [
            (active_lbl_1, ""),
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(active_data[0]["twse"]) if active_data[0]["twse"] else "無"),
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(active_data[0]["tpex"]) if active_data[0]["tpex"] else "無"),
            (active_lbl_2, ""),
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(active_data[1]["twse"]) if active_data[1]["twse"] else "無"),
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(active_data[1]["tpex"]) if active_data[1]["tpex"] else "無"),
            (active_lbl_3, ""),
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(active_data[2]["twse"]) if active_data[2]["twse"] else "無"),
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(active_data[2]["tpex"]) if active_data[2]["tpex"] else "無")
        ]
        
        if hasattr(self, "disp_active_box"):
             self.disp_active_box.update_items(active_list)
        
        # Exit Box Labels
        lbl_1 = f"明天({safe_date(0)})出關"
        lbl_2 = f"後天({safe_date(1)})出關"
        lbl_3 = f"大後天({safe_date(2)})出關"
        lbl_4 = f"前四天({safe_date(4)})出關"  # Changed from 3 to 4 - show 1 day earlier
        
        exit_list = [
            (lbl_1, ""),
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[0]["twse"]) if exit_data[0]["twse"] else "無"),
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[0]["tpex"]) if exit_data[0]["tpex"] else "無"),
            (lbl_2, ""),
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[1]["twse"]) if exit_data[1]["twse"] else "無"),
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[1]["tpex"]) if exit_data[1]["tpex"] else "無"),
            (lbl_3, ""),
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[2]["twse"]) if exit_data[2]["twse"] else "無"),
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[2]["tpex"]) if exit_data[2]["tpex"] else "無"),
            (lbl_4, ""),
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[4]["twse"]) if exit_data[4]["twse"] else "無"),  # Changed from 3 to 4
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[4]["tpex"]) if exit_data[4]["tpex"] else "無")  # Changed from 3 to 4
        ]
        
        if hasattr(self, "disp_exit_box"):
             self.disp_exit_box.update_items(exit_list)

        # SAVE SUMMARY TO DB
        date_str = self.current_display_date.strftime("%Y%m%d")
        summary_data = {
            "obs": obs_list,
            "notice": notice_list,
            "exit": exit_list if exit_list else [],
            "active": active_list if 'active_list' in locals() else []
        }
        self.cache_manager.save_dashboard_summary(date_str, summary_data)

    def show_update_help(self):
         # Moved to MainWindow
         pass

    def populate_table(self, agg_data):
        self.grid_table.setSortingEnabled(False)
        self.grid_table.setRowCount(0)

        # [Fix 2026-08-28] 「處置頻率」欄要一併標出目前這次是初犯還是累犯，跟
        # forecast_page.py 的處置中清單一致。初犯/累犯判定要用
        # ForecastWorker._predict_exact_disposal_frequency()——直接沿用同一套已經
        # 驗證過的演算法(看最近30個營業日內是否已有其他處置紀錄)，不要在這裡另外
        # 重寫一份，避免兩邊邏輯漂移。這裡先把處置紀錄整批查一次、按代碼分組，
        # 供下面逐列呼叫時查表用，不要每列各自查一次 DB(上百列會變成上百次DB往返)。
        _disp_map_for_offense = {}
        try:
            from core.disposal_database import DisposalDatabase
            _disp_db_for_offense = DisposalDatabase()
            for r in _disp_db_for_offense.get_all_records():
                _disp_map_for_offense.setdefault(str(r['code']), []).append(r)
            _disp_db_for_offense.close()
        except Exception as e:
            print(f"[populate_table] disposal_records 讀取失敗(初犯/累犯標示將略過): {e}")

        # [Crawler Integration] Fetch Official Attention List
        # [Crawler Integration] Fetch Official Attention List - ALREADY DONE in update_info_boxes
        # self.today_attention_map should be ready
        
        # Identify date columns (Initial Guess)
        raw_date_cols = self.calendar["past"] + [self.calendar["current"]]
        
        # 歷史日期 (擴展到 30 天以支持 Rule 4: 30日內12次)
        raw_pred_history_dates = []
        # [Fix] 使用當前顯示日期作為預測基準，而非固定為系統最後交易日
        # 這解決了切換歷史日期時，30日內12次規則 window 偏差的問題
        curr = self.current_display_date
        count = 0
        while count < 30:
             if DateUtils.is_trading_day(curr):
                  raw_pred_history_dates.insert(0, curr.strftime("%m/%d"))
                  count += 1
             curr = curr - dt.timedelta(days=1)
             
        # --- Dynamic Holiday Detection (DISABLED) ---
        # valid_dates = set()
        # for code, info in agg_data.items():
        #     clauses = info.get("clauses", {})
        #     for date_str, clause_val in clauses.items():
        #         if clause_val: 
        #             valid_dates.add(date_str)
                    
        # Filter date_cols
        # Use raw_date_cols directly to ensure user sees all Trading Days (even if data is empty)
        current_date_str = self.calendar["current"]
        date_cols = raw_date_cols
        
        # Filter pred_history_dates (Must keep order)
        pred_history_dates = raw_pred_history_dates
        
        # if not date_cols: date_cols = raw_date_cols
        # if not pred_history_dates: pred_history_dates = raw_pred_history_dates

        # Sort by code
        valid_keys = [str(k) for k in agg_data.keys() if isinstance(k, str) or isinstance(k, int)]
        sorted_codes = sorted(valid_keys)
        
        final_rows = [] 
        
        anchor_year = self.calendar["anchor_obj"].year
        anchor_month = self.calendar["anchor_obj"].month
        anchor_date = self.calendar["anchor_obj"]
        today_dt = dt.datetime(anchor_date.year, anchor_date.month, anchor_date.day)
        
        # Prepare Future Datetimes for Exit Calculation
        # future_dates[0] is Tomorrow, [1] is Day After, etc.
        future_dts = []
        for d_str in self.calendar["future"]:
             try:
                 # Rough Parse assuming near anchor year
                 dm = d_str.split("/")
                 m, d = int(dm[0]), int(dm[1])
                 y = anchor_year
                 if anchor_month == 12 and m == 1: y += 1
                 elif anchor_month == 1 and m == 12: y -= 1
                 future_dts.append(dt.datetime(y, m, d))
             except:
                 future_dts.append(None)

        # For Exit Box
        # Groups: 0->Tomorrow Free, 1->Day After Free, 2->3rd Day Free
        # Key: 0, 1, 2. Value: { "twse": [], "tpex": [] }
        exit_data = {
            0: {"twse": [], "tpex": []},
            1: {"twse": [], "tpex": []},
            2: {"twse": [], "tpex": []}
        }
        
        # For Observer Box
        listening_twse = []
        listening_tpex = []
        one_step_twse = []
        one_step_tpex = []
        
        # For Disposition Notice Box (Newly Announced)
        notice_twse = []
        notice_tpex = []
        
        # [Fix] Identify stocks that MUST be shown:
        # 1. Stocks on the Official Listening List for the displayed date (from listening_history.json)
        # 2. All stocks in agg_data (they came from _build_local_agg_data = attention stocks, must show)
        force_show_codes = set(agg_data.keys())  # 所有 agg_data 裡的股票都是注意股，應強制顯示
        if hasattr(self, 'history_manager'):
            try:
                recs = self.history_manager.get_listening_data(self.current_display_date)
                for r in recs:
                    force_show_codes.add(str(r['code']))
            except: pass

        for code in sorted(list(force_show_codes), key=str):
            data = agg_data.get(code, {
                "name": self.today_attention_names.get(code, ""),
                "source": self.today_attention_map.get(code, "上市"),
                "clauses": {},
                "is_disposed": False,
                "has_futures": self.mf_db.has_futures(code) if hasattr(self, 'mf_db') else False
            })
            name = data["name"]
            
            # 1. Filter Warrants and Invalid Float Codes
            if "." in str(code): continue
            if len(str(code)) > 4: continue
            if "購" in name or "售" in name: continue
            # 2. Filter DR (Unless Disposed/Notice)
            if "DR" in name:
                 # Check if this DR stock has important status to show
                 is_disp = data.get("is_disposed", False)
                 has_period = bool(data.get("period", ""))
                 if not (is_disp or has_period):
                     continue
            
            # Calculate Prediction
            # Parse Disposition Start/End Date if available
            # Parse Disposition Start/End Date
            # Prioritize explicit data from DB (e.g. for future announcements)
            p_start_val = data.get("period_start")
            p_end_val = data.get("period_end")

            disp_start_dt = None
            if p_start_val:
                try: disp_start_dt = datetime.strptime(str(p_start_val), "%Y-%m-%d")
                except Exception: disp_start_dt = DateUtils.parse_period_start(str(p_start_val))
            else:
                period = data.get("period", "")
                disp_start_dt = DateUtils.parse_period_start(period)

            disp_end_dt = None
            if p_end_val:
                try: disp_end_dt = datetime.strptime(str(p_end_val), "%Y-%m-%d")
                except: disp_end_dt = DateUtils.parse_period_end(str(p_end_val))
            else:
                 period = data.get("period", "") or data.get("future_period", "")
                 disp_end_dt = DateUtils.parse_period_end(period)
            

            hist_items = []
            clauses_map = data["clauses"].copy()  # 複製以避免修改原始資料
            
            # [Fix] 我們不再強制從 clauses_map 中刪除第九款以上或「注」，以供 30日12次 正確預測。
            # 在後續組裝 hist_items 時，會分別計算 is_any (1~8) 與 is_any_all (所有)。
            
            # [Fix] 移除對 history_manager.history 的重複遍歷。
            # 相關合併邏輯已移動到 load_data_for_date 中的 _merge_clauses_from_listening_history 完成。
            # 這大幅提升了 O(N^2) 的渲染效能。
            pass 

            
            for d in pred_history_dates:
                 # Resolve Year for d (MM/DD)
                try:
                    dm = d.split("/")
                    d_month = int(dm[0])
                    d_day = int(dm[1])
                    
                    eff_year = anchor_year
                    if anchor_month == 1 and d_month == 12:
                        eff_year -= 1
                    elif anchor_month == 12 and d_month == 1:
                        eff_year += 1
                    d_dt = datetime(eff_year, d_month, d_day)
                except Exception as e:
                    d_dt = None

                c_str = clauses_map.get(d, "")
                
                # If currently disposed, ignore clauses BEFORE disposition start
                should_reset = False
                if disp_start_dt and disp_start_dt.date() <= today_dt.date():
                    # [Fix] 處置生效日(Start Date)的盤後注意算「新週期第一次」
                    # 範例: 4/21~4/23 連續3天第一款 → 4/24 進處置
                    #       4/24 盤後又被注意 → 新週期第一次
                    #       4/25 再被注意 → 新週期第二次
                    # 所以只清空「嚴格小於」處置起始日的觸發紀錄
                    if d_dt and d_dt.date() < disp_start_dt.date(): # Exclusive Reset
                        should_reset = True
                        
                if should_reset:
                    c_str = "" # Reset
                


                is_c1 = "一" in c_str
                valid_any_list = [c for c in c_str.split(',') if c.strip() in ['一', '二', '三', '四', '五', '六', '七', '八']]
                is_any = len(valid_any_list) > 0
                is_any_all = len(c_str.strip()) > 0
                hist_items.append({"is_clause1": is_c1, "is_any": is_any, "is_any_all": is_any_all})
                
            # Limit prediction to next 5 days (Trading Week) matches user expectation for 10-day window
            warning_msg, prob, min_needed = DispositionPredictor.analyze(hist_items, future_days=5)
            
            # if code == "2408":
            #     print(f"DEBUG 2408 Prediction Result: msg='{warning_msg}', prob={prob}, needed={min_needed}", flush=True)

            # --- Override for Already Disposed Stocks ---
            # If stock is ALREADY in disposition (active), and Predictor says "Will Enter" (needed <= 0),
            # it means it has accumulated streaks DURING disposition.
            # We should NOT predict "Entering" (Red) because it's already in.
            # Instead, show nothing (Blank) as per user request to avoid confusion.
            # If Predictor says "Next X days" (Extension?), we keep it.
            if data.get("is_disposed", False) and disp_start_dt and disp_start_dt.date() <= today_dt.date():
                if min_needed <= 0: # Predicted "Enter" with high prob
                     warning_msg = "" # Suppress
                     prob = 0 # Lower priority

            # Collect Observer Data (Only for non-disposed)
            if not data.get("is_disposed", False) and not disp_start_dt:
                code_name = f"<a href='{code}' style='color: #E0E0E0; text-decoration: none;'>{code}&nbsp;{name}</a>"
                if min_needed == 1:
                    # Auto-Save to History
                    # [User Request] Disable "Smart" Auto-Save/Add. 
                    # Listening Zone must strictly follow GitHub download.
                    # try:
                    #     rec_date = self.current_display_date.strftime("%Y-%m-%d")
                    #     rec = {
                    #         "date": rec_date,
                    #         "code": code,
                    #         "name": name,
                    #         "trigger_info": json.dumps(clauses_map, ensure_ascii=False),
                    #         "is_disposed_next_day": False # Default
                    #     }
                    #     self.history_manager.add_record(rec)
                    # except Exception as e:
                    #     print(f"History Save Error: {e}")

                    # if data["source"] == "上市": listening_twse.append(code_name)
                    # else: listening_tpex.append(code_name)
                    # pass
                    
                    pass 
                    
                    # [Fix] Disable calculated add to Listening Zone.
                    # if data["source"] == "上市": listening_twse.append(code_name)
                    # else: listening_tpex.append(code_name)

                elif min_needed == 2:
                    # [Fix] 6949 Duplicate Issue
                    # check if it's already in the "Listening" lists (Official or Predicted)
                    # Note: code_name = f"<a href='{code}' ...>{code}&nbsp;{name}</a>"
                    
                    is_in_listening = False
                    # Check calculated listening list (official listening via history_manager)
                    for item in listening_twse + listening_tpex:
                        if f"{code}&nbsp;" in item:
                             is_in_listening = True
                             break
                    
                    # [Fix] 注意：不能因為股票在 today_attention_map（注意條款官方清單）就視為「已聽牌」
                    # today_attention_map 只是「注意股」，不等於「進聽牌」
                    # 只有在 listening_history 中才算正式進聽

                    if not is_in_listening:
                        if data["source"] == "上市": one_step_twse.append(code_name)
                        else: one_step_tpex.append(code_name)
            
            # --- New Disposition Override ---
            # [Fix] Check if Future Start (Notice) even if is_disposed=False
            if disp_start_dt and (data.get("is_disposed", False) or disp_start_dt.date() > today_dt.date()):
                 # Only override message for FUTURE/NEWLY ANNOUNCED (Start > Today)
                 if disp_start_dt.date() > today_dt.date():
                     prob = 100
                     if not warning_msg or "此後" not in warning_msg:
                         warning_msg = f"已進入處置 (生效日: {disp_start_dt.strftime('%m/%d')})"

                 if disp_start_dt > today_dt:
                     suffix = "(期)" if data.get("has_futures") else ""
                     item_str = f"<a href='{code}' style='color: #E0E0E0; text-decoration: none;'>{code}&nbsp;{name}{suffix}</a>"
                     if data["source"] == "上市": notice_twse.append(item_str)
                     else: notice_tpex.append(item_str)
            
            # Check for Exit (only if disposed and has end date)
            if data.get("is_disposed", False) and disp_end_dt:
                 target_idx = -1
                 if today_dt and disp_end_dt.date() == today_dt.date():
                     target_idx = 0 # End Today -> Free Tomorrow
                 elif len(future_dts) > 0 and future_dts[0] and disp_end_dt.date() == future_dts[0].date():
                     target_idx = 1 # End Tomorrow -> Free Day After
                 elif len(future_dts) > 1 and future_dts[1] and disp_end_dt.date() == future_dts[1].date():
                     target_idx = 2 # End Day After -> Free 3rd Day
                     
                 if target_idx != -1:
                     suffix = ""
                     if data.get("has_futures"): suffix += "(期)"
                     if self.cb_db.has_cb_now(code): suffix += "(CB)"
                     item_str = f"<a href='{code}' style='color: #E0E0E0; text-decoration: none;'>{code}&nbsp;{name}{suffix}</a>"
                     if data["source"] == "上市": exit_data[target_idx]["twse"].append(item_str)
                     else: exit_data[target_idx]["tpex"].append(item_str)

            has_visible_clause = False
            for d in date_cols:
                if clauses_map.get(d, ""):
                    has_visible_clause = True
                    break
            
            # If No Warning AND No Visible Clauses -> Skip (Noise)
            # EXCEPTION: If it is in force_show_codes (Official Listening List), SHOW IT even if no clauses visible locally
            should_force = code in force_show_codes
            
            if not warning_msg and not has_visible_clause and not data.get("is_disposed", False) and not should_force:
                continue

            # [New 2026-09-28] 聽牌股(明天注意就進處置)額外說明：若明天沒被注意，
            # 之後最容易進處置的條件(四條規則挑最容易的)，例如 3450 聯鈞
            # 「9/29逃過處置 : 2天內2次注意 才會進處置」。處置中的股票也要顯示(處置生效日起
            # 重新累計，例如 2305 處置中又連4次注意)；只有已公告但處置還沒開始的不顯示，
            # 因為那段期間的累計會在生效日歸零。
            # 在這個(第一個)迴圈算好存進 final_rows，避免第二個迴圈讀到殘留變數。
            escape_line = ""
            _upcoming_disposal = disp_start_dt is not None and disp_start_dt.date() > today_dt.date()
            if min_needed == 1 and warning_msg and not _upcoming_disposal:
                try:
                    _esc = DispositionPredictor.escape_tomorrow_requirement(hist_items)
                    if _esc:
                        _next_day = today_dt + dt.timedelta(days=1)
                        while not DateUtils.is_trading_day(_next_day):
                            _next_day += dt.timedelta(days=1)
                        _kind = "第一款注意" if _esc["clause1"] else "注意"
                        escape_line = (f"{_next_day.month}/{_next_day.day}逃過處置 : "
                                       f"{_esc['days']}天內{_esc['hits']}次{_kind} 才會進處置")
                except Exception as e:
                    print(f"[populate_table] {code} 逃過處置說明計算失敗: {e}")

            final_rows.append((code, data, warning_msg, prob, min_needed, escape_line))

        # Update Headers (simplified)
        headers = ["股票", "名稱", "類別", "處置頻率", "處置天數", "融券", "期貨", "CB"]
        display_date_cols = []
        if date_cols:
            for i, d in enumerate(date_cols):
                if i == len(date_cols) - 1:
                    display_date_cols.append(f"{d} (今)")
                else:
                    display_date_cols.append(d)
        headers.extend(display_date_cols)
        headers.extend(self.calendar["future"]) 
        headers.append("機率") 
        headers.append("處置預測") 
        headers.append("Original_ID") # Add Hidden Column
        
        self.grid_table.setColumnCount(len(headers))
        self.grid_table.setHorizontalHeaderLabels(headers)
        self.grid_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        
        # Reset visibility for all columns first
        for i in range(len(headers)):
            self.grid_table.setColumnHidden(i, False)
            
        self.grid_table.setColumnHidden(len(headers)-1, True)
        
        # Sort by Probability (Desc), then Code (Asc)
        final_rows.sort(key=lambda x: (-x[3], x[0]))
        
        self.grid_table.setRowCount(len(final_rows))
        print(f"[populate_table] agg_data={len(agg_data)} force_show={len(force_show_codes)} final_rows={len(final_rows)}", flush=True)
        
        for row, (code, data, warning_msg, prob, min_needed, escape_line) in enumerate(final_rows):
            is_disposed = data.get("is_disposed", False)
            
            # Manual Alternating Background
            # Manual Alternating Background
            base_bg = QColor("#2A2A2A") if row % 2 == 1 else QColor("#1E1E1E")
            
            # [Fix] 優先使用最新的「未來處置公告」來顯示 Tooltip 與頻率
            f_period = data.get("future_period", "")
            f_measure = data.get("future_measure", "")
            
            period = f_period if f_period else data.get("period", "")
            measure = f_measure if f_measure else data.get("measure", "")
            
            tooltip_txt = f"處置期間: {period}\n處置措施: {measure}" if (is_disposed or f_period) else ""

            # Determine Row Highlight:
            # 1. Pink if Disposed (含當天剛公告、處置期間尚未開始者 —
            #    [Fix 2026-08-31] 使用者要求不再區分「已生效/剛公告」兩種顏色，
            #    公告當天就統一顯示粉紅色 + 處置頻率)
            # 2. Light Blue if Listening (min_needed == 1) - User Request

            is_listening = False

            if not is_disposed and min_needed == 1:
                is_listening = True

            # Code
            code_item = QTableWidgetItem(code)
            if is_disposed:
                 code_item.setBackground(QBrush(QColor("#FFCCCC"))) # Pink
                 code_item.setForeground(QBrush(QColor("#000000")))
                 code_item.setToolTip(tooltip_txt)
            elif is_listening:
                 code_item.setBackground(QBrush(QColor("#ADD8E6"))) # Light Blue
                 code_item.setForeground(QBrush(QColor("#000000"))) # Black Text
            else:
                 code_item.setBackground(QBrush(base_bg))
                 code_item.setForeground(QBrush(QColor("#CCCCCC")))

            self.grid_table.setItem(row, 0, code_item)

            # Name
            name_item = QTableWidgetItem(data["name"])
            if is_disposed:
                 name_item.setBackground(QBrush(QColor("#FFCCCC"))) # Pink
                 name_item.setForeground(QBrush(QColor("#000000")))
                 name_item.setToolTip(tooltip_txt)
            elif is_listening:
                 name_item.setBackground(QBrush(QColor("#ADD8E6"))) # Light Blue
                 name_item.setForeground(QBrush(QColor("#000000"))) # Black Text
            else:
                 name_item.setBackground(QBrush(base_bg))
                 name_item.setForeground(QBrush(QColor("#CCCCCC")))
            self.grid_table.setItem(row, 1, name_item)
            
            # Source
            source_raw = data["source"].replace("(條款)", "").strip()
            # 正規化 source 字串（確保只顯示「上市」或「上櫃」）
            if source_raw in ("TWSE", "tse"): source_raw = "上市"
            elif source_raw in ("TPEX", "otc", "OTC"): source_raw = "上櫃"
            source_item = QTableWidgetItem(source_raw)
            source_item.setBackground(QBrush(base_bg))
            
            if "上市" in source_raw:
                source_item.setForeground(QBrush(QColor("#44FF44"))) # Green
            elif "上櫃" in source_raw:
                source_item.setForeground(QBrush(QColor("#FFCC00"))) # Yellow
            else:
                source_item.setForeground(QBrush(QColor("#CCCCCC")))
            
            self.grid_table.setItem(row, 2, source_item)
            
            # Disposition Frequency (Col 3) - NEW
            from core.measure_parser import MeasureParser
            measure = measure or ""
            freq_text = ""
            if is_disposed:
                ref_date_str = self.current_display_date.strftime("%Y-%m-%d")
                freq_text = MeasureParser.get_effective_frequency(measure, ref_date_str)

                # [Fix 2026-08-28] 附上初犯/累犯標示，跟 forecast_page.py 的處置中清單一致
                # [Fix 2026-09-04] _predict_exact_disposal_frequency() 本質是「預測」函式：
                # 給一個 anchor_date，它問的是「如果從 anchor_date(+days_until_trigger)算起
                # 觸發一次新處置，那次新處置會是初犯還是累犯」。這裡是 is_disposed=True 分支
                # (股票已經在處置中)，之前誤傳「今天」當 anchor_date——對一支已經在9/2開始
                # 處置、今天(例如9/4)還在處置期間內的股票，這樣算出來的 predicted_start_date
                # 會是「今天之後的下一個交易日」(例如9/5)，而9/2那筆處置本身正好落在這個
                # 假設的「下一次」之前，於是被誤判成「這支股票之前發生過處置」，讓正在進行
                # 中的這次處置自己被貼上「累犯」──但它問的其實是一個不存在的假設情境
                # (今天又觸發一次新處置)，不是「這次正在進行的處置」本身的初犯/累犯。
                # 已進處置的股票，正確的初犯/累犯判定基準是「這次處置自己的 period_start」，
                # 不是「今天」。改成把 anchor_date 換成 disp_start_dt 前一個交易日，讓函式內部
                # 算出的 predicted_start_date 剛好等於這次處置真正的 period_start，才能正確
                # 判定「這次」處置是初犯還是累犯。
                #
                # [Fix 2026-09-04 之二，真正的根因] populate_table() 其實有兩個獨立迴圈：
                # 第一個迴圈(逐檔計算 warning_msg/min_needed，組成 final_rows)裡才會算出
                # 正確的 disp_start_dt(每檔各自的處置起始日)；但那個變數只是迴圈內的區域
                # 變數，並沒有存進 final_rows 的 tuple 裡。這裡是第二個迴圈(逐列畫表格，
                # for row, (code, data, ...) in enumerate(final_rows))，讀到的 disp_start_dt
                # 其實是「第一個迴圈跑完後殘留的最後一筆值」，跟目前這一列的 code 完全無關
                # ——這才是真機測試 3406/6933 一直显示「累犯」、但獨立单元測試都正確算出
                # 「初犯」的真正原因(獨立測試用的是自己重建的單一 dict，沒有這種殘留變數
                # 的問題，所以測不出來)。修法：在這個迴圈裡用 data(這個變數才是正確、
                # 隨列而變的)重新算一次 disp_start_dt，不要沿用外層迴圈殘留的舊值。
                try:
                    from ui.forecast_page import ForecastWorker
                    _row_p_start_val = data.get("period_start")
                    if _row_p_start_val:
                        try:
                            _row_disp_start_dt = datetime.strptime(str(_row_p_start_val), "%Y-%m-%d")
                        except Exception:
                            _row_disp_start_dt = DateUtils.parse_period_start(str(_row_p_start_val))
                    else:
                        _row_disp_start_dt = DateUtils.parse_period_start(data.get("period", ""))

                    if _row_disp_start_dt:
                        _anchor_date_only = DateUtils.get_last_trading_day(
                            _row_disp_start_dt - dt.timedelta(days=1)
                        ).date()
                    else:
                        _anchor_date_only = dt.date(anchor_date.year, anchor_date.month, anchor_date.day)
                    _enter_freq = ForecastWorker._predict_exact_disposal_frequency(
                        code, source_raw, _anchor_date_only, _disp_map_for_offense, days_until_trigger=0
                    )
                    if "累犯" in _enter_freq:
                        freq_text = f"{freq_text}累犯"
                    elif "初犯" in _enter_freq:
                        freq_text = f"{freq_text}初犯"
                except Exception as e:
                    print(f"[populate_table] {code} 初犯/累犯標示計算失敗: {e}")
            elif min_needed in (1, 2):
                # [Fix 2026-08-31] 聽牌股(min_needed==1，只差最後一次注意就進處置)與
                # 一進聽股票(min_needed==2，最快兩次注意就進處置)尚未真的進處置，沒有
                # 實際頻率可顯示，但可以假設「明天(聽牌)/後天(一進聽)進處置」預先估算
                # 屆時是初犯還是累犯，標「(預計)」避免跟已生效的處置混淆。
                try:
                    from ui.forecast_page import ForecastWorker
                    _anchor_date_only = dt.date(anchor_date.year, anchor_date.month, anchor_date.day)
                    _days_until_trigger = 0 if min_needed == 1 else 1
                    _predicted_freq = ForecastWorker._predict_exact_disposal_frequency(
                        code, source_raw, _anchor_date_only, _disp_map_for_offense,
                        days_until_trigger=_days_until_trigger
                    )
                    if _predicted_freq:
                        freq_text = f"{_predicted_freq}(預計)"
                except Exception as e:
                    print(f"[populate_table] {code} 預計初犯/累犯標示計算失敗: {e}")

            freq_item = QTableWidgetItem(freq_text)
            freq_item.setBackground(QBrush(base_bg))
            if freq_text:
                freq_item.setForeground(QBrush(QColor("#FFCC00"))) # Gold
                freq_item.setToolTip(measure) # Show full measure on hover
            else:
                freq_item.setForeground(QBrush(QColor("#CCCCCC")))
            freq_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid_table.setItem(row, 3, freq_item)

            # [New] Disposal Day Count (Col 4) - 顯示「第X天/共Y天」
            # 只在已經進處置(is_disposed)且有明確起訖日時才顯示，跟「處置頻率」欄
            # 用同一個 is_disposed 條件，語意一致；還沒開始生效(尚未到起始日)
            # 或缺起訖日資料時留空。
            #
            # [Fix 2026-09-10] 這裡是 populate_table() 的第二個迴圈(逐列畫表格)，
            # disp_start_dt/disp_end_dt 是第一個迴圈(逐檔計算 warning_msg 那段)的
            # 區域變數、沒有存進 final_rows，在這裡讀到的其實是第一個迴圈跑完後
            # 殘留的最後一筆值，跟目前這一列的 code 無關(跟先前初犯/累犯欄位
            # 踩到的是同一個坑，見上面 2026-09-04 的說明)。改成直接用 data 重新
            # 算一次這一列自己的起訖日，不要沿用外層迴圈殘留的舊值。
            days_text = ""
            _row_p_start_val = data.get("period_start")
            if _row_p_start_val:
                try:
                    _row_disp_start_dt = datetime.strptime(str(_row_p_start_val), "%Y-%m-%d")
                except Exception:
                    _row_disp_start_dt = DateUtils.parse_period_start(str(_row_p_start_val))
            else:
                _row_disp_start_dt = DateUtils.parse_period_start(data.get("period", ""))

            _row_p_end_val = data.get("period_end")
            if _row_p_end_val:
                try:
                    _row_disp_end_dt = datetime.strptime(str(_row_p_end_val), "%Y-%m-%d")
                except Exception:
                    _row_disp_end_dt = DateUtils.parse_period_end(str(_row_p_end_val))
            else:
                _row_disp_end_dt = DateUtils.parse_period_end(data.get("period", "") or data.get("future_period", ""))

            if is_disposed and _row_disp_start_dt and _row_disp_end_dt:
                def _count_trading_days_inclusive(s_dt, e_dt):
                    if not s_dt or not e_dt or s_dt.date() > e_dt.date():
                        return 0
                    n = 0
                    cur = s_dt
                    while cur.date() <= e_dt.date():
                        if DateUtils.is_trading_day(cur):
                            n += 1
                        cur += dt.timedelta(days=1)
                    return n

                total_days = _count_trading_days_inclusive(_row_disp_start_dt, _row_disp_end_dt)
                if today_dt.date() < _row_disp_start_dt.date():
                    days_text = ""  # 已公告但還沒生效，跟「處置頻率」欄一起留空
                else:
                    effective_today = _row_disp_end_dt if today_dt.date() > _row_disp_end_dt.date() else today_dt
                    current_day = _count_trading_days_inclusive(_row_disp_start_dt, effective_today)
                    if total_days > 0:
                        days_text = f"第{current_day}天/共{total_days}天"

            days_item = QTableWidgetItem(days_text)
            days_item.setBackground(QBrush(base_bg))
            if days_text:
                days_item.setForeground(QBrush(QColor("#FFCC00")))
            else:
                days_item.setForeground(QBrush(QColor("#CCCCCC")))
            days_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid_table.setItem(row, 4, days_item)

            # Short Selling Status (Col 5) - 改從 mf_db 讀取
            can_short = self.mf_db.is_margin_stock(code)
            # Fallback: 若 DB 沒有資料，用 agg_data 的記錄
            if not can_short:
                can_short = data.get("can_short", False)
            short_item = QTableWidgetItem("可" if can_short else "")
            short_item.setBackground(QBrush(base_bg))
            if can_short:
                short_item.setForeground(QBrush(QColor("#44FF44")))
                short_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                 short_item.setForeground(QBrush(QColor("#CCCCCC")))
            self.grid_table.setItem(row, 5, short_item)

            # Futures Status (Col 6) - 改從 mf_db 讀取
            has_futures = self.mf_db.has_futures(code)
            futures_item = QTableWidgetItem("有" if has_futures else "")
            futures_item.setBackground(QBrush(base_bg))
            if has_futures:
                 futures_item.setForeground(QBrush(QColor("#4da6ff")))
                 futures_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                 futures_item.setForeground(QBrush(QColor("#CCCCCC")))
            self.grid_table.setItem(row, 6, futures_item)

            # CB Status (Col 7)
            has_cb = self.cb_db.has_cb_now(code)
            cb_item = QTableWidgetItem("CB" if has_cb else "")
            cb_item.setBackground(QBrush(base_bg))
            if has_cb:
                 cb_item.setForeground(QBrush(QColor("#FF9944")))  # 橘色（與期貨藍色做區別）
                 cb_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                 cb_item.setForeground(QBrush(QColor("#CCCCCC")))
            self.grid_table.setItem(row, 7, cb_item)

            # Dates (Restored QLabel for HTML styling)
            # Dates (Restored QLabel for HTML styling)
            col_idx = 8
            bg_hex = base_bg.name() # e.g. #2A2A2A
            
            date_col_map = {} # Map date_str -> col_idx for highlighting
            
            for d in date_cols:
                date_col_map[d] = col_idx # Store index
                
                clause_str = data["clauses"].get(d, "")
                
                html_parts = []
                if clause_str:
                    clauses = clause_str.split(",")
                    for c in clauses:
                        c = c.strip()
                        if c in ["注", "九", "十", "十一", "十二"]:
                            continue # [Fix] 表格中不顯示這些無特定含意的條款，減少畫面雜亂
                        elif c == "一":
                            html_parts.append(f"<span style='color: #FF4444; font-weight: bold;'>{c}</span>")
                        else:
                            html_parts.append(f"<span style='color: #4da6ff;'>{c}</span>")
                
                final_html = ",".join(html_parts)
                lbl = QLabel(final_html)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                # Set background to match row
                lbl.setStyleSheet(f"QLabel {{ background-color: {bg_hex}; border: none; font-size: 14px; }}")
                
                # Add Sort Item (Hidden value for sorting)
                sort_val = 0
                if "一" in clause_str: sort_val = 2
                elif clause_str: sort_val = 1
                
                sort_item = SortableWidgetItem()
                sort_item.setData(Qt.ItemDataRole.UserRole, sort_val) # Numeric sort (UserRole)
                sort_item.setData(Qt.ItemDataRole.DisplayRole, sort_val) 
                sort_item.setForeground(QBrush(QColor(0,0,0,0))) # Hidden text
                self.grid_table.setItem(row, col_idx, sort_item)
                
                self.grid_table.setCellWidget(row, col_idx, lbl)
                col_idx += 1
            
            # Future days
            for d in self.calendar["future"]:
                date_col_map[d] = col_idx # Store index
                
                lbl = QLabel("-")
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet(f"QLabel {{ background-color: {bg_hex}; border: none; color: #555; }}")
                self.grid_table.setCellWidget(row, col_idx, lbl)
                col_idx += 1
                
            # --- Highlight 10th Trading Day from First Attention ---
            full_timeline = pred_history_dates + self.calendar["future"]
            
            # Find FIRST hit in full_timeline
            first_hit_idx = -1
            for idx, d_str in enumerate(full_timeline):
                # Check clauses (clauses_map has history+current)
                # Note: 'data["clauses"]' only has Past/Current data.
                c_str = data["clauses"].get(d_str, "")
                if c_str:
                    first_hit_idx = idx
                    break
            
            if first_hit_idx != -1:
                target_idx = first_hit_idx + 9 # 1st + 9 days = 10th day
                if target_idx < len(full_timeline):
                    target_date = full_timeline[target_idx]
                    
                    # Highlight if this date is visible in table
                    if target_date in date_col_map:
                        t_col = date_col_map[target_date]
                        widget = self.grid_table.cellWidget(row, t_col)
                        if widget:
                            # Append Blue Background style
                            # We need to preserve existing border/font styles, but override background
                            current_style = widget.styleSheet()
                            # Simply append to overwrite background-color
                            new_style = current_style + " QLabel { background-color: #2b5797; }" 
                            widget.setStyleSheet(new_style)
                
            # Prob (Restored QLabel)
            prob_lbl = QLabel(f"{prob}%" if warning_msg else "")
            prob_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            prob_style = "QLabel { border: none; font-size: 14px; font-weight: bold; "
            if prob == 100:
                 # Fully Red for 100%
                 prob_style += "background-color: #FF4444; color: #FFFFFF; border-radius: 4px; }"
            elif prob >= 80:
                 prob_style += f"background-color: {bg_hex}; color: #FF4444; }}"
            elif prob >= 50:
                 prob_style += f"background-color: {bg_hex}; color: #FFAA00; }}"
            else:
                 prob_style += f"background-color: {bg_hex}; color: #44FF44; }}"
            
            prob_lbl.setStyleSheet(prob_style)
            
            prob_lbl.setStyleSheet(prob_style)
            
            # Sort Item for Probability
            prob_item = SortableWidgetItem()
            prob_item.setData(Qt.ItemDataRole.UserRole, prob) # UserRole
            prob_item.setData(Qt.ItemDataRole.DisplayRole, prob)
            self.grid_table.setItem(row, col_idx, prob_item)
            
            self.grid_table.setCellWidget(row, col_idx, prob_lbl)
            col_idx += 1
            
            # Prediction (Restored QLabel with WordWrap)
            # [Crawler Integration] Check Official Reason
            official_reason = self.today_attention_map.get(code, "")
            
            # Prioritize Official Reason if available
            final_msg = warning_msg
            is_official = False
            
            if official_reason:
                final_msg = official_reason # Show official reason directly
                is_official = True
            elif is_listening:
                 # Keep existing prediction logic if no official reason
                 pass

            if escape_line:
                # 官方原因可能是 rich text，換行要用 <br>；純文字用 \n
                _sep = "<br>" if ("<" in final_msg and ">" in final_msg) else "\n"
                final_msg = f"{final_msg}{_sep}{escape_line}" if final_msg else escape_line

            pred_lbl = QLabel(final_msg)
            pred_lbl.setWordWrap(True)
            pred_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            pred_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
             
            if is_official or "已達" in final_msg or "已進入處置" in final_msg:
                # Highlight "Reached" or "Entered Disposition"
                pred_lbl.setStyleSheet("QLabel { border: none; background-color: #FFFFCC; color: #000000; font-size: 12px; font-weight: bold; }")
            else:
                # Normal Warning
                pred_lbl.setStyleSheet(f"QLabel {{ border: none; background-color: {bg_hex}; color: #FFAAAA; font-size: 12px; }}")
            
            # Sort Item for Prediction
            pred_item = QTableWidgetItem(final_msg)
            self.grid_table.setItem(row, col_idx, pred_item)
                
            self.grid_table.setCellWidget(row, col_idx, pred_lbl)
            
            # Original ID (Hidden)
            col_idx += 1
            orig_item = SortableWidgetItem()
            orig_item.setData(Qt.ItemDataRole.UserRole, row) # UserRole
            orig_item.setData(Qt.ItemDataRole.DisplayRole, row)
            self.grid_table.setItem(row, col_idx, orig_item)
            
            self.grid_table.setRowHeight(row, 75 if escape_line else (60 if warning_msg else 30))

        # self.grid_table.setSortingEnabled(True) # DIY Sorting
        


        # [Auto-Update] Update History Status based on fetched data
        self.update_history_status(agg_data)

        # [Fix] Cache Agg Data for ALL dates (not just today)
        # This allows historical data to persist across restarts
        date_str = self.current_display_date.strftime('%Y%m%d')
        self.cache_manager.save_agg_data(date_str, agg_data)

    def update_history_status(self, agg_data):
        """Update historical records if they entered disposition"""
        if not self.history_manager: return
        
        records = self.history_manager.get_all()
        start_dates = {} # code -> start_date (datetime)

        # 1. Parse all start dates from agg_data (which reflects TODAY's status)
        for code, info in agg_data.items():
            if info.get("is_disposed") and info.get("period"):
                start = DateUtils.parse_period_start(info["period"])
                if start:
                    start_dates[code] = start

        changes = 0
        for r in records:
            # Skip if already marked
            if r.get("is_disposed_next_day"): continue
            
            code = r["code"]
            if code in start_dates:
                # Check if this record's date is "related" to the start date
                try:
                    r_date = datetime.strptime(r["date"], "%Y-%m-%d")
                    s_date = start_dates[code]
                    
                    # If Disposition Start is AFTER Record Date, and within reasonable range (e.g. 7 days)
                    # Example: Record 1/5. Disposition Start 1/6. Diff 1 day.
                    # We check s_date.date() > r_date.date() just to be safe
                    if s_date.date() > r_date.date() and (s_date - r_date).days <= 7:
                        r["is_disposed_next_day"] = True
                        changes += 1
                except:
                    pass
        
        if changes > 0:
            self.history_manager.save()
            print(f"Updated {changes} history records with disposition status.")

    def change_date(self, offset):
        new_date = self.current_display_date + dt.timedelta(days=offset)
        self.load_data_for_date(new_date)

    def open_history_page(self):
        pass # Integrated into Tab
        
    def on_github_sync_click(self):
        """Handle GitHub Sync Button Click"""
        date_str = self.current_display_date.strftime("%Y-%m-%d")
        
        reply = QMessageBox.question(
            self, 
            "確認下載覆蓋", 
            f"確定要從 GitHub 下載並**覆蓋** {date_str} 的聽牌資料嗎？\n\n警告：此操作將清除您對該日期所做的任何本地編輯。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.status_message_updated.emit(f"正在從 GitHub 同步 {date_str} 資料...")
            QApplication.processEvents() # Force UI update
            
            success, msg = self.history_manager.force_sync_date(self.current_display_date)
            
            if success:
                QMessageBox.information(self, "同步成功", msg)
                
                # [Fix] Clean Cache to ensure fresh load
                # Force delete cache for this date so reload_data will rebuild from history
                # [Fix] Clean Cache to ensure fresh load
                # Force delete cache for this date so reload_data will rebuild from history
                # Fix: Use correct attribute name 'cache_manager' and correct date format 'YYYYMMDD'
                cache_obj = getattr(self, 'cache_manager', getattr(self, 'cache_mgr', None))
                
                if cache_obj:
                    # DB uses YYYYMMDD format
                    cache_date_str = self.current_display_date.strftime("%Y%m%d") 
                    
                    try:
                        cache_obj.delete_daily_data(cache_date_str)
                        cache_obj.delete_agg_data(cache_date_str) 
                        cache_obj.delete_dashboard_summary(cache_date_str)
                        # print(f"DEBUG: Cleared cache for {cache_date_str}")
                    except Exception as e:
                        print(f"Cache Clear Error: {e}")
                
                # Refresh Data
                self.load_data_for_date(self.current_display_date)
            else:
                QMessageBox.critical(self, "同步失敗", f"錯誤: {msg}")
            
            self.status_message_updated.emit("就緒")

    def on_download_clauses_click(self):
        """下載過去 9 個交易日的注意條款（第 1-8 款，4 碼股票）"""
        date_str = self.current_display_date.strftime("%Y/%m/%d")
        
        reply = QMessageBox.question(
            self, 
            "下載注意條款", 
            f"確定要下載 {date_str} 往前推 8 個交易日的注意條款嗎？\n\n• 只下載第 1-8 款條款\n• 只處理 4 碼股票（排除權證）\n\n預計花費 30-60 秒。",

            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.Yes
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # 計算往前 8 個交易日（總共 9 個交易日）
        from core.utils import DateUtils
        dates = []
        current = self.current_display_date
        
        # 加入當前日期
        if DateUtils.is_trading_day(current):
            dates.append(current)
        
        # 往前推 8 個交易日
        count = 0
        test_date = current - dt.timedelta(days=1)
        while count < 8:
            if DateUtils.is_trading_day(test_date):
                dates.append(test_date)
                count += 1
            test_date = test_date - dt.timedelta(days=1)
        
        # 反轉順序（從舊到新）
        dates.reverse()
        
        start_str = dates[0].strftime('%m/%d')
        end_str = dates[-1].strftime('%m/%d')
        
        self.status_message_updated.emit(f"正在下載注意條款 ({start_str} ~ {end_str}, 共 {len(dates)} 個交易日)...")
        
        # Disable buttons
        self.btn_clause_download.setEnabled(False)
        self.btn_official_disposal.setEnabled(False)
        self.refresh_date_btn.setEnabled(False)
        
        # Start Worker
        from ui.clause_download_worker import ClauseDownloadWorker
        self.clause_worker = ClauseDownloadWorker(dates)
        self.clause_worker.progress.connect(lambda msg: self.status_message_updated.emit(msg))
        self.clause_worker.finished.connect(self.on_clause_download_finished)
        self.clause_worker.start()

    def on_clause_download_finished(self, success, msg):
        """下載完成後的處理"""
        # Re-enable buttons
        self.btn_clause_download.setEnabled(True)
        self.btn_official_disposal.setEnabled(True)
        self.refresh_date_btn.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "下載完成", msg + "\n\n注意：條款已儲存到資料庫，但目前表格不會自動顯示。\n請使用資料庫工具查詢 attention_clauses 資料表。")
            self.status_message_updated.emit("就緒")
        else:
            QMessageBox.critical(self, "下載失敗", msg)
            self.status_message_updated.emit("錯誤")


    def on_update_margin_futures_click(self):
        """
        [NEW] 更新融券/期貨清單按鈕
        從台灣證交所、櫃買中心、期交所下載最新資料，
        覆蓋儲存到 margin_futures.db，並立即刷新 UI。
        """
        from PyQt6.QtWidgets import QMessageBox

        self.btn_update_mf.setEnabled(False)
        self.btn_update_mf.setText("更新中...")
        self.status_message_updated.emit("正在更新融券/期貨資料...")
        QApplication.processEvents()

        try:
            fetcher = StockFetcher()
            parser = StockParser()
            date_str = self.current_display_date.strftime("%Y%m%d")

            # 1. 下載融券清單（TWSE + TPEX）
            self.status_message_updated.emit("正在下載融券清單...")
            QApplication.processEvents()
            margin_codes = set()

            twse_margin = fetcher.fetch_twse_margin_list(date_str)
            if twse_margin:
                margin_codes.update(parser.parse_twse_margin(twse_margin))

            tpex_margin = fetcher.fetch_tpex_margin_list(date_str)
            if tpex_margin:
                margin_codes.update(parser.parse_tpex_margin(tpex_margin))

            # 2. 下載期貨清單（台期所）
            self.status_message_updated.emit("正在下載期貨清單...")
            QApplication.processEvents()
            futures_codes = set()

            taifex_futures = fetcher.fetch_taifex_futures_list()
            if taifex_futures:
                futures_codes.update(parser.parse_taifex_futures_list(taifex_futures))

            # 若 API 沒回傳期貨清單，fallback 使用靜態清單
            if not futures_codes:
                from core.futures_stocks import FUTURES_STOCKS
                futures_codes = set(FUTURES_STOCKS)
                print(f"[UpdateMF] 期交所 API 無資料，使用靜態清單 ({len(futures_codes)} 筆)")

            # 3. 儲存到 DB（覆蓋舊資料）
            self.status_message_updated.emit("正在儲存到資料庫...")
            QApplication.processEvents()
            self.mf_db.save_margin_stocks(margin_codes)
            self.mf_db.save_futures_stocks(futures_codes)

            # 4. 刷新 UI（InfoBox + 表格）
            self.status_message_updated.emit("正在刷新顯示...")
            QApplication.processEvents()
            current_agg = getattr(self, 'agg_data', {})
            self.update_info_boxes(current_agg)
            self.populate_table(current_agg)

            # 5. 顯示成功訊息
            msg = (
                f"融券/期貨清單更新完成！\n\n"
                f"• 融券可交易：{len(margin_codes)} 支股票\n"
                f"• 股票期貨：{len(futures_codes)} 支股票\n\n"
                f"資料來源：{date_str[:4]}/{date_str[4:6]}/{date_str[6:]} 的盤後資料"
            )
            self.status_message_updated.emit("就緒")
            QMessageBox.information(self, "更新完成", msg)

        except Exception as e:
            self.status_message_updated.emit("更新失敗")
            QMessageBox.critical(self, "更新失敗", f"下載融券/期貨資料時發生錯誤：\n{e}")
            import traceback
            traceback.print_exc()
        finally:
            self.btn_update_mf.setEnabled(True)
            self.btn_update_mf.setText("更新融券/期貨")

    def on_official_disposal_click(self):
        """下載官方處置公告 (上市/上櫃)"""
        date_str = self.current_display_date.strftime("%Y%m%d")
        display_str = self.current_display_date.strftime("%Y/%m/%d")
        
        reply = QMessageBox.question(
            self, 
            "下載官方處置", 
            f"確定要從 證交所/櫃買中心 下載 {display_str} 的處置公告嗎？\n\n這將更新本地處置資料庫。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
            
        try:
            self.status_message_updated.emit(f"正在下載 {display_str} 官方處置公告...")
            QApplication.processEvents()
            
            fetcher = StockFetcher()
            parser = StockParser()
            db = DisposalDatabase() # Initialize connection
            
            # 1. Fetch Listing (Twse)
            self.status_message_updated.emit(f"正在下載上市處置公告...")
            QApplication.processEvents()
            twse_raw = fetcher.fetch_twse_disposition(date_str)
            twse_records = parser.parse_twse_disposition(twse_raw)
            
            # [Fallback] 使用 Shioaji 補齊上市處置股
            if not twse_records:
                try:
                    from core.shioaji_client import ShioajiClient
                    from datetime import datetime
                    sj = ShioajiClient()
                    punish_df = sj.get_punish()
                    if punish_df is not None and not punish_df.empty:
                        for _, row in punish_df.iterrows():
                            p_code = str(row.get("code", "")).strip()
                            source = fetcher.check_market_type(p_code)
                            if source != "上市": continue
                            
                            s_dt = row.get("start_date")
                            e_dt = row.get("end_date")
                            period = ""
                            if s_dt and e_dt:
                                try:
                                    s_obj = datetime.strptime(str(s_dt), "%Y-%m-%d")
                                    e_obj = datetime.strptime(str(e_dt), "%Y-%m-%d")
                                    period = f"{s_obj.year-1911}/{s_obj.strftime('%m/%d')}~{e_obj.year-1911}/{e_obj.strftime('%m/%d')}"
                                except Exception: pass
                                
                            interval = row.get("interval", "")
                            measure = f"{interval}分盤" if interval else "處置中"
                            
                            # 注意: dashboard_box 需要 ann_date 才能顯示 (必須匹配 display_str)
                            # 如果是當天的處置，start_date 通常就是今天或前一個交易日。
                            # 為了讓它能在UI顯示，我們將 ann_date 設為 display_str
                            twse_records.append({
                                'code': p_code,
                                'name': p_code, 
                                'ann_date': display_str, # 強制讓它能顯示在 UI 上
                                'period': period,
                                'measure': measure,
                                'source': '上市',
                                'reason': "Shioaji 補充"
                            })
                except Exception as e:
                    print(f"Shioaji 補充失敗: {e}")
            
            print(f"DEBUG: Fetched {len(twse_records)} Listing records")
            
            # 2. Fetch OTC (Tpex)
            self.status_message_updated.emit(f"正在下載上櫃處置公告...")
            QApplication.processEvents()
            tpex_raw = fetcher.fetch_tpex_disposition(date_str)
            tpex_records = parser.parse_tpex_disposition(tpex_raw)
            print(f"DEBUG: Fetched {len(tpex_records)} OTC records")
            
            # 3. Save to DB
            all_records = twse_records + tpex_records
            
            # [Fix] Partial Update Logic (User Request: Only update Notice Area)
            # We explicitly merge the new disposal info into the EXISTING cache
            # ensuring we don't wipe out One-Step/Listening data (which might be from GitHub).
            
            if all_records:
                # 直接使用 SQL INSERT OR REPLACE 來更新資料庫
                cursor = db.conn.cursor()
                count = 0
                for r in all_records:
                    try:
                        # [Fix] parser 回傳 'ann_date' 而非 'announce_date'，需兩者都嘗試
                        # TWSE: ann_date 是 parse_roc_date 回傳的西元年字串 (2026/01/09)
                        # TPEX: ann_date 同上
                        announce_date_val = r.get('ann_date', r.get('announce_date', ''))
                        
                        # period_raw：TWSE 用 period，TPEX 也用 period
                        period_raw_val = r.get('period', r.get('period_raw', ''))
                        
                        # period_start / period_end：若沒有就從 DisposalDatabase 解析
                        p_start = r.get('period_start', '')
                        p_end = r.get('period_end', '')
                        if not p_start and period_raw_val:
                            try:
                                from core.utils import DateUtils
                                p_start_dt = DateUtils.parse_period_start(period_raw_val)
                                p_end_dt = DateUtils.parse_period_end(period_raw_val)
                                if p_start_dt: p_start = p_start_dt.strftime('%Y-%m-%d')
                                if p_end_dt: p_end = p_end_dt.strftime('%Y-%m-%d')
                            except: pass
                        
                        cursor.execute("""
                            INSERT OR REPLACE INTO disposal_records
                            (source, announce_date, code, name, period_start, period_end,
                             period_raw, measure, reason, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """, (
                            r.get('source', ''),
                            announce_date_val or p_start,
                            r.get('code', ''),
                            r.get('name', ''),
                            p_start,
                            p_end,
                            period_raw_val,
                            r.get('measure', ''),
                            r.get('reason', '')
                        ))
                        count += 1

                        # [Fix] 表格(處置預測/機率欄)只吃 self.agg_data，不會自動同步剛下載的公告，
                        # 否則會出現「盤後處置公告」已顯示、但下方表格仍顯示舊的預測機率的矛盾畫面。
                        # 這裡直接把剛存進 DB 的起訖日同步進 agg_data，稍後統一 populate_table 重繪。
                        if p_start:
                            r_code = str(r.get('code', '')).strip()
                            if r_code:
                                if r_code not in self.agg_data:
                                    self.agg_data[r_code] = {
                                        "code": r_code,
                                        "name": r.get('name', ''),
                                        "source": r.get('source', ''),
                                        "clauses": {},
                                        "trigger_info": {},
                                        "is_disposed": False,
                                    }
                                self.agg_data[r_code]["period_start"] = p_start
                                self.agg_data[r_code]["period_end"] = p_end
                                self.agg_data[r_code]["future_period"] = period_raw_val
                                self.agg_data[r_code]["future_measure"] = r.get('measure', '')
                                self.agg_data[r_code]["measure"] = r.get('measure', self.agg_data[r_code].get('measure', ''))
                    except Exception as e:
                        print(f"儲存記錄失敗 ({r.get('code', 'Unknown')}): {e}")
                
                db.conn.commit()
                msg = f"成功下載並更新 {count} 筆處置公告\n(上市: {len(twse_records)}, 上櫃: {len(tpex_records)})"
                
                # [Fix] 只更新「盤後處置公告」區塊，不重新載入整個表格（避免干擾使用者表格操作）
                cache_obj = getattr(self, 'cache_manager', getattr(self, 'cache_mgr', None))
                if cache_obj:
                    cache_obj.delete_daily_data(date_str)
                    cache_obj.delete_agg_data(date_str)
                    cache_obj.delete_dashboard_summary(date_str)
                
                # 直接擷取下載的新資料，渲染到盤後公告區塊
                notice_twse = []
                notice_tpex = []
                
                for r in twse_records:
                    c = str(r.get('code', '')).strip()
                    n = r.get('name', '')
                    if len(c) != 4: 
                        continue
                    
                    # 只顯示當天進處置的股票
                    announce_date_val = r.get('ann_date', r.get('announce_date', ''))
                    if announce_date_val != display_str:
                        continue
                        
                    suffix = ""
                    if hasattr(self, 'mf_db') and self.mf_db.has_futures(c): suffix += "(期)"
                    if hasattr(self, 'cb_db') and self.cb_db.has_cb_now(c): suffix += "(CB)"
                    notice_twse.append(f"<a href='{c}' style='color: #E0E0E0; text-decoration: none;'>{c}&nbsp;{n}{suffix}</a>")
                    
                for r in tpex_records:
                    c = str(r.get('code', '')).strip()
                    n = r.get('name', '')
                    if len(c) != 4: 
                        continue
                        
                    # 只顯示當天進處置的股票
                    announce_date_val = r.get('ann_date', r.get('announce_date', ''))
                    if announce_date_val != display_str:
                        continue
                        
                    suffix = ""
                    if hasattr(self, 'mf_db') and self.mf_db.has_futures(c): suffix += "(期)"
                    if hasattr(self, 'cb_db') and self.cb_db.has_cb_now(c): suffix += "(CB)"
                    notice_tpex.append(f"<a href='{c}' style='color: #E0E0E0; text-decoration: none;'>{c}&nbsp;{n}{suffix}</a>")
                    
                notice_list = [
                    ("上市", ""),
                    ("     ", "&nbsp;&nbsp;&nbsp;&nbsp;".join(notice_twse) if notice_twse else "無"),
                    ("上櫃", ""),
                    ("     ", "&nbsp;&nbsp;&nbsp;&nbsp;".join(notice_tpex) if notice_tpex else "無")
                ]
                self.disposition_box.update_items(notice_list)

                # [Fix] 重繪表格，讓「處置預測/機率」欄同步反映剛下載的官方公告，
                # 避免上方公告區已更新、下方表格卻還顯示下載前的舊預測。
                try:
                    if hasattr(self, 'agg_data') and self.agg_data:
                        self.populate_table(self.agg_data)
                except Exception as e:
                    print(f"[OfficialDisposal] 表格重繪失敗: {e}")
            else:
                msg = f"該日期 ({display_str}) 查無任何處置公告"

            db.close()
            
            QMessageBox.information(self, "下載完成", msg)
            
        except Exception as e:
            QMessageBox.critical(self, "錯誤", f"下載失敗: {e}")
            import traceback
            traceback.print_exc()
            
        finally:
            self.status_message_updated.emit("就緒")

    def toggle_calendar(self):
        try:
            # Simple Dialog with Calendar
            dlg = QDialog(self)
            dlg.setWindowTitle("選擇日期")
            dlg.setWindowFlags(Qt.WindowType.Popup) # Make it look like a popup
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(0, 0, 0, 0)
            
            cal = QCalendarWidget()
            cal.setSelectedDate(QDate(self.current_display_date.year, self.current_display_date.month, self.current_display_date.day))
            cal.clicked.connect(lambda d: dlg.accept())
            layout.addWidget(cal)
            
            # Position relative to button
            if self.date_btn:
                pos = self.date_btn.mapToGlobal(self.date_btn.rect().bottomLeft())
                dlg.move(pos)
            
            if dlg.exec():
                selected = cal.selectedDate()
                new_date = dt.datetime(selected.year(), selected.month(), selected.day())
                self.load_data_for_date(new_date)
        except Exception as e:
            print(f"CRASH in toggle_calendar: {e}")
            import traceback
            traceback.print_exc()

        # Mouse tracking for hover effect (already enabled in init properties)
        # self.grid_table.setMouseTracking(True)
        
        # Calculation Cache
        self.calc_cache = {}

    def load_layout_mock(self):
        # ... (mock func) ...
        pass

    # --- Slots ---
    def on_date_btn_clicked(self):
        # ... (Show Calendar) ...
        # (Simplified for brevity, assuming existing logic)
        self.calendar.show()
        
    def on_date_selected(self, qdate):
        t_date = datetime(qdate.year(), qdate.month(), qdate.day())
        self.load_data_for_date(t_date)

        self.load_data_for_date(t_date)

    def _merge_disposal_status_from_db(self, agg_data, target_date):
        """
        將 disposal_records 的公告(announce_date)與現行(active)處置狀態，
        即時合併進 agg_data(就地修改)。

        [Fix 2026-08-31] 原本這段邏輯只在 _build_local_agg_data() 的「無快取」
        回退路徑才會執行；但只要當天的 agg_cache 曾經被建立過(即使是在該股票
        公告要處置「之前」建的舊底稿)，就會走 cached_agg 分支直接沿用舊資料，
        永遠不會重新從 disposal_records 同步 is_disposed/future_period，導致
        當天公告的新處置股(例：3163/3441/4188/6103 於 2026-08-28 公告)在總覽
        與儀表板都顯示不出來。抽成獨立方法，讓 cached_agg 分支也能呼叫，確保
        不論是否有快取，處置狀態都以 disposal_records(即時資料庫)為準。
        """
        date_str = target_date.strftime("%Y-%m-%d")
        try:
            db = DisposalDatabase()

            # A. Fetch Announcements (Notices for today)
            # [Fix 2026-09-05 撤回] 原本加了 get_upcoming_disposals() 把「公告日不是
            # 今天、但生效日還沒到」的個股也算進來，這是誤解需求——使用者明確要求
            # 「盤後處置公告」只顯示「今天」盤後公告的個股，隔天(即使該個股生效日
            # 還沒到)就不該再顯示，要換成隔天自己當天公告的個股。維持單純只查
            # announce_date == 今天，不額外擴大範圍。
            ann_recs = db.get_disposal_by_announce_date(date_str)

            # B. Fetch Active Disposals (For Table/Exit Zone)
            act_recs = db.get_active_disposals(date_str)

            all_disposals = []
            # [Fix] 使用 (code, period_start) 做記錄去重，避免 ann_recs 和 act_recs 重複
            ann_keys = set()
            act_keys = set()
            if ann_recs:
                for r in ann_recs:
                    key = (r['code'], r.get('period_start', ''))
                    ann_keys.add(key)
                    all_disposals.append(r)
            if act_recs:
                for r in act_recs:
                    key = (r['code'], r.get('period_start', ''))
                    act_keys.add(key)
                    if key not in ann_keys:  # 避免重複加入
                        all_disposals.append(r)

            # Sort all collected records by announce_date DESC, then period_start DESC
            # So the FIRST record we see for a code is the NEWEST (most recently announced/started)
            def safe_date(d): return d if d else "0000-00-00"
            all_disposals.sort(key=lambda x: (safe_date(x.get('announce_date')), safe_date(x.get('period_start'))), reverse=True)

            seen_codes = set()
            for r in all_disposals:
                code = r['code']
                r_key = (code, r.get('period_start', ''))
                is_announcement = r_key in ann_keys
                is_active = r_key in act_keys

                # Setup code entry if missing
                if code not in agg_data:
                    # [Fix] 從 futures_stocks 模組檢查期貨
                    has_futures = False
                    try:
                        from core.futures_stocks import has_futures as check_futures
                        has_futures = check_futures(code)
                    except: pass

                    can_short = False

                    agg_data[code] = {
                        "code": code,
                        "name": r['name'],
                        "source": r['source'],
                        "clauses": {},
                        "trigger_info": {},
                        # [Fix 2026-08-31] 公告當天(即使處置期間尚未開始)也視為已處置，
                        # 統一以粉紅色 + 處置頻率顯示，不再區分「已生效/剛公告」兩種顏色
                        # (使用者要求：顏色直接顯示粉紅即可，但要有處置頻率)。
                        "is_disposed": is_active or is_announcement,
                        "has_futures": has_futures,
                        "can_short": can_short
                    }
                    seen_codes.add(code)

                    # Fill primary data from the NEWEST record
                    agg_data[code]["measure"] = r.get("measure", "")
                    agg_data[code]["source"] = r["source"]

                    if is_announcement:
                        agg_data[code]["future_period"] = r.get("period_raw", r.get("period", ""))
                        agg_data[code]["future_measure"] = r.get("measure", "")
                        agg_data[code]["period_start"] = r.get("period_start")
                        agg_data[code]["period_end"] = r.get("period_end")
                        if not is_active:
                            agg_data[code]["period"] = r.get("period_raw", r.get("period", ""))
                    if is_active:
                        agg_data[code]["period"] = r.get("period_raw", r.get("period", ""))
                else:
                    # If this is the FIRST time we process this code in this loop
                    if code not in seen_codes:
                        seen_codes.add(code)
                        agg_data[code]["is_disposed"] = is_active or is_announcement
                        agg_data[code]["measure"] = r.get("measure", "")
                        agg_data[code]["source"] = r["source"]

                        if is_announcement:
                            agg_data[code]["future_period"] = r.get("period_raw", r.get("period", ""))
                            agg_data[code]["future_measure"] = r.get("measure", "")
                            agg_data[code]["period_start"] = r.get("period_start")
                            agg_data[code]["period_end"] = r.get("period_end")
                            if not is_active:
                                agg_data[code]["period"] = r.get("period_raw", r.get("period", ""))
                        if is_active:
                            agg_data[code]["period"] = r.get("period_raw", r.get("period", ""))
                    else:
                        # Existing Code, seen_codes ALREADY HAS IT meaning we already filled the NEWEST measure.
                        # Do not overwrite measure. Just supplement Missing status.
                        if (is_active or is_announcement) and not agg_data[code].get("is_disposed"):
                            agg_data[code]["is_disposed"] = True
                            if not agg_data[code].get("period"):
                                agg_data[code]["period"] = r.get("period_raw", r.get("period", ""))

                        if is_announcement and not agg_data[code].get("period_start"):
                             agg_data[code]["future_period"] = r.get("period_raw", r.get("period", ""))
                             agg_data[code]["period_start"] = r.get("period_start")
                             agg_data[code]["period_end"] = r.get("period_end")

                # Ensure checking Short and Futures from history or module update here too if desired
                # [Fix] 即使已存在，也要檢查並更新期貨與融券資訊
                if "has_futures" not in agg_data[code] or not agg_data[code]["has_futures"]:
                    try:
                        from core.futures_stocks import has_futures as check_futures
                        agg_data[code]["has_futures"] = check_futures(code)
                    except: pass

                # 融券檢測稍後統一處理

            db.close()

            # [Fix] Restore Clauses from History for Active Disposals
            # (Because Disposal DB doesn't have daily clause details, but History does)
            active_codes = set(k for k, v in agg_data.items() if v.get("is_disposed"))

            if active_codes and hasattr(self, 'history_manager'):
                 last_recs = {}
                 for h in self.history_manager.history:
                     c = str(h.get("code"))
                     if c in active_codes:
                         d_str = h.get("date", "")
                         # Find latest record before or on target date
                         if d_str <= date_str:
                             curr_best = last_recs.get(c)
                             if not curr_best or d_str > curr_best["date"]:
                                 last_recs[c] = h

                 for c, h in last_recs.items():
                     t_info = {}
                     try:
                         raw = h.get("trigger_info", {})
                         if isinstance(raw, str): t_info = json.loads(raw)
                         else: t_info = raw
                     except: pass

                     if t_info:
                         # Merge clauses into active disposal data
                         current_clauses = agg_data[c].get("clauses", {})
                         # If current is empty, just take it. If not, merge?
                         if not current_clauses:
                              agg_data[c]["clauses"] = t_info
                              agg_data[c]["trigger_info"] = t_info # Keep sync

        except Exception as e:
            print(f"Error loading disposal DB for {target_date}: {e}")

        return agg_data

    def _build_local_agg_data(self, target_date):
        """
        從本地檔案 (History + Disposal DB) 重建 agg_data
        用於當快取被清除時的回退機制，確保資料不消失。
        """
        agg_data = {}
        date_str = target_date.strftime("%Y-%m-%d") # Format for DB query (flexible)
        
        # 1. Load Listening History in Range (Past 15 days)
        # This ensures the table shows all stocks noticed recently, not just today's.
        try:
            from datetime import timedelta
            start_dt = target_date - timedelta(days=15)
            start_str = start_dt.strftime("%Y-%m-%d")
            target_str = target_date.strftime("%Y-%m-%d")
            
            # Group by code, AGGREGATE records in range
            # (Solves Missing Clauses by merging dates, Solves Futures by scanning all history)
            map_agg = {} 
            
            if hasattr(self, 'history_manager'):
                 # Sort by date
                 sorted_hist = sorted(self.history_manager.history, key=lambda x: x.get("date", ""))
                 
                 for h in sorted_hist:
                     d = h.get("date", "")
                     c = str(h.get("code"))
                     
                     if start_str <= d <= target_str:
                         if c not in map_agg:
                             map_agg[c] = {
                                 "code": c,
                                 "name": h.get("name"),
                                 "source": h.get("source"),
                                 "has_futures": False,
                                 "clauses": {},
                                 "tags": [],
                                 "comment": "",
                                 "last_date": ""
                             }
                         
                         entry = map_agg[c]
                         
                         # 1. Accumulate Futures (If any record says True, it's True)
                         if h.get("has_futures"):
                             entry["has_futures"] = True
                             
                         # 2. Accumulate Clauses (Trigger Info)
                         raw = h.get("trigger_info", {})
                         t_dict = {}
                         if isinstance(raw, str):
                             try:
                                 t_dict = json.loads(raw)
                             except:
                                 # [Fix] Fallback: 從純文字描述中萌取「第一至八款」
                                 # 例: 「……（第六款）」 → key=「02/04」 val=「六」
                                 import re as _re
                                 CLAUSE_MAP = {
                                     "一": "一", "二": "二", "三": "三", "四": "四",
                                     "五": "五", "六": "六", "七": "七", "八": "八"
                                 }
                                 # 找出所有「第X款」，X 限一至八
                                 clause_matches = _re.findall(r'第([一二三四五六七八])款', raw)
                                 if clause_matches and d and len(d) >= 10:
                                     k = f"{d[5:7]}/{d[8:10]}"
                                     clause_val = ",".join(CLAUSE_MAP[c] for c in clause_matches)
                                     t_dict[k] = clause_val
                                 # [Fix] 純文字 trigger_info fallback：
                                 # 「連續X次」= 當天一定是注意日 → 標記為「注」
                                 # 「已有X次」= 可能只是窗口滑動 → 不做 fallback
                                 elif "連續" in raw and d and len(d) >= 10:
                                     k = f"{d[5:7]}/{d[8:10]}"
                                     t_dict[k] = "注"
                         else:
                             t_dict = raw
                         
                         if t_dict:
                             for k, v in t_dict.items():
                                 # Normalize Key: 20260120 -> 01/20
                                 norm_k = k
                                 if len(k) == 8 and k.isdigit(): # YYYYMMDD
                                     norm_k = f"{k[4:6]}/{k[6:]}"
                                 elif "-" in k and len(k)==10: # YYYY-MM-DD
                                      norm_k = f"{k[5:7]}/{k[8:10]}"
                                 
                                 # Normalize Value
                                 val_str = str(v)
                                 # Map "第一款" -> "一"
                                 val_str = val_str.replace("第一款", "一").replace("第二款", "二") \
                                                  .replace("第三款", "三").replace("第四款", "四") \
                                                  .replace("第五款", "五").replace("第六款", "六") \
                                                  .replace("第七款", "七").replace("第八款", "八") \
                                                  .replace("第九款", "九").replace("第十款", "十") \
                                                  .replace("第十一款", "十一").replace("第十二款", "十二")
                                 
                                 # [User Request] Remove "連續" / "注意" - showing nothing is better than showing noise
                                 if "連續" in val_str: val_str = ""
                                 if val_str == "詳見紀錄": val_str = ""
                                 # [Fix] 「注意」是不精確的泛稱，不存入 clauses
                                 # 只有第 1~8 款才有意義
                                 if val_str == "注意": val_str = ""
                                 
                                 if val_str:
                                     entry["clauses"][norm_k] = val_str
                                 
                         # 3. Update Metadata if Newer
                         if d >= entry["last_date"]:
                             entry["last_date"] = d
                             entry["name"] = h.get("name")
                             if h.get("source"): entry["source"] = h.get("source") # Prefer latest source
                             entry["tags"] = h.get("tags")
                             entry["comment"] = h.get("comment")
                             # [Simple Fix] 直接從 history 讀取期貨與融券資訊
                             if "has_futures" in h:
                                 entry["has_futures"] = h.get("has_futures", False)
                             if "can_short" in h:
                                 entry["can_short"] = h.get("can_short", False)

            # [Simple Fix] 設定預設值
            for code in map_agg:
                if "has_futures" not in map_agg[code]:
                    map_agg[code]["has_futures"] = False
                if "can_short" not in map_agg[code]:
                    map_agg[code]["can_short"] = False

            for code, data in map_agg.items():
                t_info = data["clauses"]
                
                # Resolve Source (prefer Agg > Search > Default)
                # [Fix] FinMind is not a displayable source, resolve to 上市/上櫃
                src = data.get("source")
                if not src or src == "FinMind":
                     # 1. Try PriceDatabase (Local Cache)
                     try:
                         pdb = PriceDatabase()
                         src = pdb.get_latest_source(code)
                         pdb.close()
                     except: pass
                     
                     # 2. [效能優化] 移除同步 check_market_type() 網路請求
                     # 每支股票約 500ms，20 支就要 10 秒以上，導致日期切換嚴重卡頓
                     # 改為直接使用預設值，確保日期切換即時響應
                     if not src or src == "FinMind":
                         src = "上市"

                agg_data[code] = {
                    "code": code,
                    "name": data['name'],
                    "source": src,
                    "trigger_info": t_info,
                    "clauses": t_info, # [Fix] Use aggregated clauses
                    "tags": data.get("tags", []),
                    "comment": data.get("comment", ""),
                    "is_disposed": False, 
                    "has_futures": data.get("has_futures", False), # Accumulate True
                    "can_short": data.get("can_short", False)
                }
        except Exception as e:
            print(f"Error loading history for {target_date}: {e}")

        # 2. Load Disposal Announcements (DB) & Active
        self._merge_disposal_status_from_db(agg_data, target_date)

        return agg_data

    def _merge_clauses_from_listening_history(self, agg_data, target_date):
        if not hasattr(self, 'history_manager') or not self.history_manager:
            return
            
        import json
        import datetime as _dt
        from datetime import timedelta
        
        # [Fix] 擴展到 45 天以覆蓋 30 日內 12 次規則所需的完整範圍
        lookback_days = 45
        start_date = target_date - timedelta(days=lookback_days)
        current = start_date
        
        # 計算 target_date 的年份與月份，用於解析 MM/DD 格式的日期鍵
        t_year = target_date.year
        t_month = target_date.month
        
        while current <= target_date:
            records = self.history_manager.get_listening_data(current)
            if records:
                for r in records:
                    code = r["code"]
                    if len(str(code)) == 5: continue
                    
                    trigger_info = r.get("trigger_info", {})
                    rec_date_str = r.get("date", "")
                    if isinstance(trigger_info, str):
                        try: trigger_info = json.loads(trigger_info)
                        except:
                            trigger_info = {}
                        
                    if code in agg_data and trigger_info:
                        for date_key, clause_val in trigger_info.items():
                            # 只補充精確的條款（1~8款），跳過「注」「注意」
                            if clause_val in ("注", "注意"):
                                continue
                            
                            # [Fix] 只填入 ≤ target_date 的條款，避免未來日期污染預測結果
                            # 例如 GitHub 資料裡 "2026-02-03" 的記錄含有 "02/04" 的條款
                            # 這種「預告」條款不應填入今天的預測
                            try:
                                parts = date_key.split("/")
                                if len(parts) == 2:
                                    m, d = int(parts[0]), int(parts[1])
                                    # 處理跨年邊界
                                    y = t_year
                                    if t_month == 1 and m == 12:
                                        y = t_year - 1
                                    elif t_month == 12 and m == 1:
                                        y = t_year + 1
                                    clause_dt = _dt.datetime(y, m, d)
                                    # 只允許 ≤ target_date 的條款
                                    if clause_dt.date() > target_date.date():
                                        continue
                            except:
                                pass  # 日期解析失敗時不過濾（容錯）
                            
                            if date_key not in agg_data[code]["clauses"] or not agg_data[code]["clauses"][date_key]:
                                agg_data[code]["clauses"][date_key] = clause_val
            current += timedelta(days=1)


    def _merge_clauses_from_db(self, agg_data, target_date):
        """
        從資料庫查詢注意條款並合併到 agg_data 的 clauses 字典中
        查詢過去 10 個交易日的所有條款
        
        Args:
            agg_data: 聚合資料字典
            target_date: datetime 物件
        """
        try:
            import sqlite3
            from core import database_clause_ext
            from core.utils import DateUtils
            import datetime as dt
            
            # 連接資料庫
            conn = sqlite3.connect("data/disposal_history.db")
            
            # 計算過去 30 個交易日 (支援 Rule 4: 30日內12次)
            past_dates = []
            curr = target_date
            count = 0
            while count < 30:
                if DateUtils.is_trading_day(curr):
                    past_dates.append(curr)
                    count += 1
                curr = curr - dt.timedelta(days=1)
            
            # 查詢每個交易日的條款
            total_loaded = 0
            for date_obj in past_dates:
                date_str_mmdd = date_obj.strftime("%m/%d")
                clauses_from_db = database_clause_ext.get_clauses_for_date(conn, date_str_mmdd)
                
                if not clauses_from_db:
                    continue
                # 將從資料庫取得的條款加入 agg_data
                for code, db_data in clauses_from_db.items():
                    clause_val = db_data.get("clauses", "")
                    # [Fix] 如果該股票不在 agg_data 中，將其加入以便「一進聽」預測可以執行
                    if code not in agg_data:
                        agg_data[code] = {
                            "name": db_data.get("name", ""),
                            "source": db_data.get("source", "上市"), 
                            "clauses": {},
                            "disposition": None,
                            "is_disposed": False,
                            "period": "",
                            "can_short": False,
                            "has_futures": False
                        }
                    
                    data = agg_data[code]
                    if not isinstance(data, dict):
                        continue
                        
                    # [Fix] 如果快取中的名稱是空的，用資料庫的名稱補上
                    if not data.get("name") and db_data.get("name"):
                        data["name"] = db_data.get("name")
                        
                    if "clauses" not in data:
                        data["clauses"] = {}
                        
                    # 覆蓋或加入該日期的條款
                    data["clauses"][date_str_mmdd] = clause_val
                    total_loaded += 1
            
            conn.close()
            
            if total_loaded > 0:
                print(f"[Clauses] 已從資料庫載入過去 30 個交易日共 {total_loaded} 筆條款資料")
            
        except Exception as e:
            print(f"[Clauses] 從資料庫載入條款失敗: {e}")
            import traceback
            traceback.print_exc()

    def load_data_for_date(self, target_date):
        try:
            print(f"=" * 60)
            print(f"DEBUG: load_data_for_date called with date: {target_date}")
            print(f"DEBUG: Current display date before: {self.current_display_date}")
            
            self.current_display_date = target_date
            # Clear Cache on Date Change
            self.calc_cache.clear()
            
            date_str = target_date.strftime("%Y%m%d")
            display_str = target_date.strftime("%Y/%m/%d")
            print(f"DEBUG: date_str={date_str}, display_str={display_str}")
            self.date_btn.setText(f"{display_str} ▼")
            
            # --- [Fix] Sync everything to Target Date ---
            # Update Headers to match target date (Calendar + Columns)
            self.update_headers_for_date(target_date)
            
            cached_agg = self.cache_manager.get_agg_data(date_str)
            summary = self.cache_manager.get_dashboard_summary(date_str)
            
            # Use cached data if available
            if cached_agg:
                 # [Fix] 補入 listening_history 中當日有記錄但 cache 缺少的注意股
                 # 例如 HistoricalDataRefreshWorker 當時 API 未回傳的 TWSE 股票
                 if hasattr(self, 'history_manager'):
                     date_recs = self.history_manager.get_listening_data(target_date)
                     missing_added = 0
                     for rec in date_recs:
                         code = str(rec.get('code', '')).strip()
                         if code and code not in cached_agg:
                             cached_agg[code] = {
                                 "name": rec.get("name", ""),
                                 "source": rec.get("source", "上市"),
                                 "clauses": {},
                                 "is_disposed": False,
                                 "period": "",
                             }
                             missing_added += 1
                     if missing_added > 0:
                         print(f"[load_data_for_date] 補入 {missing_added} 支 listening_history 注意股到 cache_agg")
                 
                 # 從資料庫載入注意條款並合併到 agg_data
                 self._merge_clauses_from_db(cached_agg, target_date)
                 # [Fix] 從 listening_history.json 補充條款（修正處置股條款空白問題，如 1727, 3363, 8291）
                 self._merge_clauses_from_listening_history(cached_agg, target_date)
                 # [Fix 2026-08-31] cached_agg 可能是「該股票公告要處置之前」建立的舊底稿，
                 # 若不重新同步，當天公告的新處置股會永遠顯示不出來（is_disposed/
                 # future_period 停留在快取當下的空值）。改以 disposal_records(即時
                 # 資料庫)為準，重新合併處置狀態。
                 self._merge_disposal_status_from_db(cached_agg, target_date)

                 self.update_info_boxes(cached_agg)
                 self.populate_table(cached_agg)
            elif summary:
                 # If only summary exists (rare, usually agg exists if summary does)
                 self.observer_box.update_items(summary.get("obs", {}))
                 self.disposition_box.update_items(summary.get("notice", {}))
                 if hasattr(self, "disp_active_box"):
                      self.disp_active_box.update_items(summary.get("active", {}))
                 if hasattr(self, "disp_exit_box"):
                      self.disp_exit_box.update_items(summary.get("exit", {}))
                 
                 # Logic Decision: If we have summary but no agg, maybe we should trigger download?
                 # ideally start_worker will handle "no data" case if user clicks, 
                 # or we can clear the table to indicate "No Data loaded".
                 self.grid_table.setRowCount(0) 
            else:
                 # No cached data - Try to rebuild from local files (History + DB)
                 print(f"DEBUG: No cache for {date_str}, rebuilding from local storage")
                 
                 fallback_agg = self._build_local_agg_data(target_date)
                 
                 # 從資料庫載入注意條款並合併到 agg_data
                 self._merge_clauses_from_db(fallback_agg, target_date)
                 # [Fix] 從 listening_history.json 補充條款
                 self._merge_clauses_from_listening_history(fallback_agg, target_date)
                 
                 self.update_info_boxes(fallback_agg)
                 
                 # Also show in table if we have data
                 if fallback_agg:
                      self.populate_table(fallback_agg)
                 else:
                      self.grid_table.setRowCount(0)

        except Exception as e:
            print(f"CRASH in load_data_for_date: {e}")
            import traceback
            traceback.print_exc()


    def on_header_clicked(self, logical_index):
        # 3-State Sorting: Asc -> Desc -> Reset
        
        # If clicking a new column, start with Asc
        if logical_index != self._cur_sort_col:
            self._cur_sort_col = logical_index
            self._cur_sort_order = Qt.SortOrder.AscendingOrder
            self.grid_table.sortItems(logical_index, self._cur_sort_order)
            self.grid_table.horizontalHeader().setSortIndicator(logical_index, self._cur_sort_order)
        else:
            # Same column
            if self._cur_sort_order == Qt.SortOrder.AscendingOrder:
                # Go to Desc
                self._cur_sort_order = Qt.SortOrder.DescendingOrder
                self.grid_table.sortItems(logical_index, self._cur_sort_order)
                self.grid_table.horizontalHeader().setSortIndicator(logical_index, self._cur_sort_order)
            else:
                # Go to Reset (Sort by hidden Original_ID)
                self._cur_sort_col = -1
                self._cur_sort_order = Qt.SortOrder.AscendingOrder
                
                # Clear Indicator
                self.grid_table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
                
                # Sort by hidden last col
                last_col = self.grid_table.columnCount() - 1
                self.grid_table.sortItems(last_col, Qt.SortOrder.AscendingOrder)

    # ==================== 搜尋功能 ====================
    
    def search_stock(self):
        """處理個股搜尋邏輯"""
        stock_code = self.search_input.text().strip()
        
        if not stock_code:
            QMessageBox.warning(self, "輸入錯誤", "請輸入股票代碼")
            return
        
        # 1. 檢查是否已在表格中
        if self._is_stock_in_table(stock_code):
            self.highlight_stock(stock_code)
            self.status_message_updated.emit(f"股票 {stock_code} 已在表格中")
            return
        
        # 2. 檢查快取
        date_str = self.current_display_date.strftime("%Y%m%d")
        cache_key = f"{stock_code}_{date_str}"
        
        if cache_key in self.search_cache:
            stock_data = self.search_cache[cache_key]
            self.display_search_result(stock_code, stock_data)
            self.status_message_updated.emit(f"從快取載入 {stock_code} 資料")
            return
        
        # 3. 下載資料
        self.status_message_updated.emit(f"正在下載 {stock_code} 資料...")
        self.fetch_stock_data_async(stock_code)
    
    def clear_search(self):
        """清空搜尋結果"""
        self.search_input.clear()
        self.search_result_box.setVisible(False)
        self.searched_stocks.clear()
        self.status_message_updated.emit("已清空搜尋結果")
    
    def fetch_stock_data_async(self, stock_code):
        """下載個股過去10天的歷史條款資料"""
        try:
            # 使用獨立模組抓取歷史資料
            stock_data = StockSearchHelper.fetch_stock_history(
                stock_code, 
                self.current_display_date, 
                days=10
            )
            
            if stock_data:
                # 儲存到快取
                date_str = self.current_display_date.strftime("%Y%m%d")
                cache_key = f"{stock_code}_{date_str}"
                self.search_cache[cache_key] = stock_data
                
                # 格式化並顯示搜尋結果
                display_items = StockSearchHelper.format_search_result(stock_code, stock_data)
                self.search_result_box.update_items(display_items)
                self.search_result_box.setVisible(True)
                
                # 標記為搜尋新增
                self.searched_stocks.add(stock_code)
                
                # 更新狀態
                days_count = len(stock_data.get('clauses', {}))
                self.status_message_updated.emit(f"已載入 {stock_code} 過去{days_count}天資料")
            else:
                QMessageBox.information(
                    self, 
                    "查無資料", 
                    f"股票 {stock_code} 在過去10天內沒有注意條款資料"
                )
                self.status_message_updated.emit(f"{stock_code} 查無資料")
        
        except Exception as e:
            QMessageBox.critical(self, "下載失敗", f"無法下載 {stock_code} 資料：{e}")
            self.status_message_updated.emit(f"下載 {stock_code} 失敗")
            print(f"DEBUG: fetch_stock_data error: {e}")
            import traceback
            traceback.print_exc()
    
    
    def display_search_result(self, stock_code, stock_data):
        """顯示搜尋結果"""
        try:
            name = stock_data.get("name", "")
            source = stock_data.get("source", "")
            clauses = stock_data.get("clauses", {})
            
            display_items = []
            display_items.append(("股票代碼", stock_code))
            display_items.append(("股票名稱", name))
            display_items.append(("類別", source))
            
            if clauses:
                clause_str = ", ".join([f"{date}: {clause}" for date, clause in clauses.items()])
                display_items.append(("注意條款", clause_str))
            else:
                display_items.append(("注意條款", "無"))
            
            self.search_result_box.update_items(display_items)
            self.search_result_box.setVisible(True)
        
        except Exception as e:
            print(f"DEBUG: display_search_result error: {e}")
    
    def _is_stock_in_table(self, stock_code):
        """檢查股票是否已在表格中"""
        for row in range(self.grid_table.rowCount()):
            item = self.grid_table.item(row, 0)
            if item and item.text().strip() == stock_code.strip():
                return True
        return False

# --- Worker ---
from core.fetcher import StockFetcher
from core.calculator import DispositionCalculator
from core.predictor import DispositionPredictor
from core.cache import CacheManager
from core.utils import DateUtils

class CalculationWorker(QThread):
    result_ready = pyqtSignal(tuple) # (calc_lines, excl_lines)
    
    def __init__(self, code, source, stock_name=None, target_date=None):
        super().__init__()
        self.code = code
        self.source = source
        self.stock_name = stock_name
        self.target_date = target_date
        
    def run(self):
        fetcher = StockFetcher()
        # Fetch 180 days for calculation (Need 60-90 days ref)
        df, shares = fetcher.fetch_stock_history(self.code, self.source, period="180d")
        
        if df is None or df.empty:
            self.result_ready.emit((self.code, (["無法取得歷史股價"], []))) # Fix tuple unpacking
            return
            
        # [Fix] Apply 18:00 cutoff logic OR Historical Date Logic
        # Truncate dataframe to ensure we don't peek into "Future" relative to view date
        if self.target_date:
             cutoff_date = self.target_date
        else:
             cutoff_date = DateUtils.get_last_trading_day()
        # Convert cutoff_date (datetime) to pd.Timestamp for comparison
        cutoff_ts = pd.Timestamp(cutoff_date)
        # Ensure we cover the whole cutoff day (normalize happens in fetcher usually, but safe check)
        cutoff_ts = cutoff_ts.normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        
        if df.index.max() > cutoff_ts:
             df = df[df.index <= cutoff_ts]
             
        if df.empty:
            self.result_ready.emit((self.code, (["資料截斷後為空"], [])))
            return

        needed_c1 = 1
        needed_any = 1
        
        # Calculate needed counts from history (from Cache)
        try:
             cache = CacheManager()
             
             # [Fix] Use Target Date for "Today" reference in history calculation
             today = self.target_date if self.target_date else dt.datetime.now()
             
             history_items = []
             
             has_c1_30 = False
             has_c2_disp_60 = False
             
             # --- Live History Check (User Requirement: Website Source) ---
             try:
                 d60_ago = today - dt.timedelta(days=90)
                 s_date = d60_ago.strftime("%Y%m%d")
                 e_date = today.strftime("%Y%m%d")
                 cutoff_30 = (today - dt.timedelta(days=30)).strftime("%Y%m%d")
                 cutoff_60 = (today - dt.timedelta(days=60)).strftime("%Y%m%d")
                 
                 # 1. Attention (Rule A)
                 live_att_map = {} 

                 hist_att = None
                 for _attempt in range(3):
                     try:
                         hist_att = fetcher.fetch_stock_attention_history(self.code, s_date, e_date, self.source)
                         if hist_att:
                             break
                         time.sleep(0.5)
                     except Exception as e:
                         print(f"Fetch Att Attempt {_attempt+1} failed: {e}")
                         time.sleep(0.5)
                         
                 if hist_att:
                     rows = []
                     if isinstance(hist_att, dict):
                         if 'data' in hist_att: rows = hist_att['data'] # TWSE
                         elif 'tables' in hist_att and len(hist_att['tables'])>0: rows = hist_att['tables'][0].get('data', []) # TPEX
                     elif isinstance(hist_att, list): rows = hist_att
                     
                     for r in rows:
                         found_date = ""
                         if isinstance(r, list) and len(r)>5: found_date = str(r[5])
                         elif isinstance(r, dict): found_date = str(r.get("Date", ""))
                         
                         if isinstance(r, list) and len(r)>1 and str(r[1]).strip() == self.code:
                             pass
                         else:
                             # TPEX check?
                             if isinstance(r, dict):
                                 c = r.get("Code", "") or r.get("code", "") or r.get("StkNo", "")
                                 if str(c).strip() == self.code: pass
                                 else: continue
                             else:
                                 continue 
                         
                         ad_date = ""
                         found_date = found_date.replace(".", "/")
                         if "/" in found_date:
                             ps = found_date.split('/')
                             if len(ps)==3: 
                                 ad_date = f"{int(ps[0])+1911}{ps[1].zfill(2)}{ps[2].zfill(2)}"
                         
                         if ad_date:
                             live_att_map[ad_date] = str(r)
                             if ad_date >= cutoff_30:
                                 r_str = str(r)
                                 if "第一款" in r_str or "第1款" in r_str:
                                     has_c1_30 = True
                 
                 # 2. Disposition (Rule B)
                 hist_disp = fetcher.fetch_stock_disposition_history(self.code, s_date, e_date, self.source)
                 if hist_disp:
                     rows_d = []
                     if isinstance(hist_disp, dict) and 'data' in hist_disp: rows_d = hist_disp['data']
                     elif isinstance(hist_disp, list): rows_d = hist_disp
                     
                     for r in rows_d:
                         found_date = ""
                         if isinstance(r, list):
                             # [Fix] TWSE Disposition API: Index 1=Date, Index 2=Code
                             if len(r) > 2:
                                 found_date = str(r[1])
                                 if str(r[2]).strip() != self.code:
                                     continue
                         elif isinstance(r, dict): 
                             found_date = str(r.get("Date", ""))
                             if str(r.get("code", "")).strip() != self.code: 
                                  c = r.get("Code", "") or r.get("code", "") or r.get("StkNo", "")
                                  if c and str(c).strip() != self.code:
                                      continue
                         
                         ad_date = ""
                         found_date = found_date.replace(".", "/")
                         if "/" in found_date:
                             ps = found_date.split('/')
                             if len(ps)==3: ad_date = f"{int(ps[0])+1911}{ps[1].zfill(2)}{ps[2].zfill(2)}"
                             
                         if ad_date and ad_date >= cutoff_60:
                             r_str = str(r)
                             if "第二款" in r_str or "第2款" in r_str or ("款" in r_str and "二" in r_str) or \
                                "第二次處置" in r_str or "六個營業日" in r_str:
                                 has_c2_disp_60 = True
                                 break
             except Exception as e:
                 print(f"Live Check Error: {e}")
             
             # --- End Live Check ---

             
             # Look back 60 days (Extended for Rule B)
             # [Fix] Range must include 0 (Today) to count today's live data in accumulation
             for i in range(60, -1, -1):
                 d = today - dt.timedelta(days=i)
                 d_str = d.strftime("%Y%m%d")
                 data = cache.get_daily_data(d_str)
                 
                 # [Fix] Inject live data if present (Ensure Today is counted)
                 if d_str in live_att_map:
                     if not data: data = []
                     # Check if we already have this stock
                     target = next((x for x in data if x.get("code") == self.code), None)
                     if target:
                         # Force update reason from live source (Cache might be stale/empty reason)
                         target["reason"] = live_att_map[d_str]
                         # if self.code == "2408": print(f"[DEBUG] Updated Cache Reason for {d_str}")
                     else:
                         # Append mock object
                         data.append({"code": self.code, "reason": live_att_map[d_str]})
                         # if self.code == "2408": print(f"[DEBUG] Injected Live Data for {d_str}")

                 found_for_day = False
                 if data:
                     target = next((x for x in data if x.get("code") == self.code), None)
                     if target:
                         # Parse Reason from Cache (Fix for missing boolean flags)
                         raw_reason = str(target.get("reason", ""))
                         c_str = ClauseParser.parse_clauses(raw_reason)
                         
                         is_c1 = ("一" in c_str)
                         # [Fix] If in live_att_map, it IS an attention, so counts as 'Any' even if parser fails
                         is_any = (len(c_str) > 0) or (d_str in live_att_map)
                         
                         # Check 30-day window for Rule A (Clause 1)
                         if i <= 30 and is_c1:
                             has_c1_30 = True
                             
                         # Check 60-day window for Rule B (Disposition via Clause 2)
                         # Logic: Check if "處置" and ("二" or "2") in reason text
                         if "處置" in raw_reason and ("二" in raw_reason or "2" in raw_reason):
                             has_c2_disp_60 = True
                         
                         history_items.append({
                             "date": d_str,
                             "is_clause1": is_c1,
                             "is_any": is_any
                         })
                         found_for_day = True
                 
                 # If not found but IS a trading day, append Empty Record (Break Streak)
                 if not found_for_day:
                     if DateUtils.is_trading_day(d):
                         history_items.append({
                             "date": d_str,
                             "is_clause1": False,
                             "is_any": False
                         })
             
             # Now calculate status
             if history_items:
                  needed_c1, needed_any = DispositionPredictor.get_status_counts(history_items)
             else:
                  # No history found (Safe default)
                  needed_c1, needed_any = 3, 5
                  
        except Exception as e:
             print(f"Worker History Error: {e}")
             needed_c1, needed_any = 3, 5
        
        # IMPORTANT FIX: 如果從 API 找到第一款記錄（has_c1_30=True），
        # 但 cache 沒有歷史（needed_c1=3 預設值），則調整為 needed_c1=1
        # 這樣可以正確處理新進入聽牌區的股票
        if has_c1_30 and needed_c1 > 1:
            print(f"[DEBUG] {self.code} has_c1_30=True，調整 needed_c1: {needed_c1} -> 1")
            needed_c1 = 1

        # [Fix 2026-09-17] needed_c1 有上面那段「即時 API 找不到就用 has_c1_30 校正」的
        # 保護，但 needed_any（連5日/10日6次/30日12次任一款）沒有同等保護——這裡的
        # history_items 是靠 fetch_stock_attention_history() 即時逐日查詢組出來的，
        # 這個 API 若某幾天查不到資料(官方端不穩定、或該股票查詢紀錄本身不完整)，
        # 對應天數就會被誤標成「當天沒有任一款觸發」，導致 streak_any/window_any
        # 被低估、needed_any 被高估。已確認的實例：3441 官方是「連5日任一款」聽牌
        # (rule2 needed=1)，但這裡舊邏輯算出 needed_any 偏高，讓 calculate_conditions()
        # 的 [2]~[8] 款因為 needed_any 沒通過 <=2 的門檻而完全不顯示，使用者只看得到
        # [1]，誤以為只有第一款會觸發，但官方「聽牌」定義是「任一款都會觸發」。
        # 改成：拿 dashboard/forecast 那條已經驗證過的可靠路徑(agg_data 快取裡的
        # trigger_progress，來源是 attention_clauses 資料庫，不受即時 API 查詢不穩定
        # 影響)算出的 needed_any 來校正，兩者取「較急迫(較小)」的那個，只會讓顯示
        # 更完整，不會讓真的還沒接近的股票被誤判成聽牌。
        try:
            _cache_date = self.target_date if self.target_date else DateUtils.get_last_trading_day()
            _cache_date_str = _cache_date.strftime("%Y%m%d")
            _cached_agg = cache.get_agg_data(_cache_date_str)
            if _cached_agg and self.code in _cached_agg:
                _tp = _cached_agg[self.code].get("trigger_progress")
                if _tp:
                    _cached_needed_any = min(
                        _tp.get("rule2", {}).get("needed", 99),
                        _tp.get("rule3", {}).get("needed", 99),
                        _tp.get("rule4", {}).get("needed", 99),
                    )
                    if _cached_needed_any < needed_any:
                        print(f"[DEBUG] {self.code} 即時查詢算出的 needed_any={needed_any} 比快取"
                              f"trigger_progress算出的{_cached_needed_any}保守，改用快取的值")
                        needed_any = _cached_needed_any
        except Exception as e:
            print(f"[DEBUG] {self.code} 校正 needed_any 失敗(不影響原本結果): {e}")

        # Calculate
        lines, is_clause2_risk, _excl_lines = DispositionCalculator.calculate_conditions(
            df, self.source, shares, needed_c1=needed_c1, needed_any=needed_any, stock_name=self.stock_name
        )
        
        # Calculate Exclusion (Pass History Flags + Clause 2 Risk)
        # 修正: 聽牌狀態 (needed_any <= 1 或 needed_c1 <= 1) 下，必須強制計算並顯示排除條件
        # 即使目前預測漲幅未達標，或系統對 needed_any 計算較保守(如 2)，
        # 只要有一款聽牌，用戶就需要看到排除條件以求安心。
        should_check_exclusion = is_clause2_risk or (needed_any <= 2) or (needed_c1 <= 2)
        if self.code == "2408":
            print(f"[DEBUG 2408] needed_c1={needed_c1}, needed_any={needed_any}, is_risk={is_clause2_risk}, should={should_check_exclusion}")
            print(f"Live Att Map Keys: {list(live_att_map.keys())}")
            
            # Print history item dates to see what was counted
            # We didn't store dates in history_items, but we can reconstruct or simple count
            print(f"History Items Count: {len(history_items)}")
            try:
                print(f"Last 10 Items: {[(x.get('date'), x.get('is_any')) for x in history_items[-10:]]}")
            except Exception as e:
                print(f"Debug Items Error: {e}")
        
        # [8046 Case Fix]
        # 如果進處置的主要原因是 [1-1] (差價條款)，通常是因為下跌或震盪觸發，與漲幅過大(第二款)無關。
        # 此時若 is_clause2_risk 為 False (即漲幅未達標)，則不應顯示排除條件，避免混淆。
        is_risk_diff_clause = False
        for line in lines:
            if "[1-1]" in line:
                is_risk_diff_clause = True
                break
                
        if is_risk_diff_clause and not is_clause2_risk:
             should_check_exclusion = False
        
        # print(f"[DEBUG_EXCL] Code={self.code}, is_risk={is_clause2_risk}, needed={needed_any}, needed_c1={needed_c1}, should={should_check_exclusion}")
        
        excl_lines = self.check_exclusion_rules(df, has_c1_30, has_c2_disp_60, should_check_exclusion)
        
        # print(f"[DEBUG_EXCL] Result Lines: {len(excl_lines)}")
        
        # Emit (Code, ResultTuple) to avoid cache poisoning
        self.result_ready.emit((self.code, (lines, excl_lines)))     

    def check_exclusion_rules(self, df, has_c1_30=False, has_c2_disp_60=False, is_clause2_risk=False):
        """
        Check Exclusion Rules for Clause 2.
        Only calculated if 'is_clause2_risk' is True (Entering via Clause 2).
        """
        if not is_clause2_risk:
            return [] # Don't show anything if not relevant to Clause 2 risk
            
        lines = []
        try:
            # Indices: T(0) ... T-6(-7). Total 7 rows needed.
            # Safety check
            if df is None or len(df) < 7:
                return ["資料不足無法計算排除條件"]
                
            today_row = df.iloc[-1] # T
            price_t = today_row['Close']
            price_t_1 = df.iloc[-2]['Close'] # T-1
            
            # Calculate Sum ROC 5 (Include T: T, T-1, T-2, T-3, T-4)
            sum_roc = 0.0
            for i in range(0, 5): # 0,1,2,3,4
                curr_idx = -(1 + i) # -1(T), -2(T-1)...
                prev_idx = -(2 + i) # -2, -3...
                p_curr = df.iloc[curr_idx]['Close']
                p_prev = df.iloc[prev_idx]['Close']
                sum_roc += (p_curr / p_prev - 1) * 100
                
            # Rule A Thresholds
            limit_rate_a = 25.0 if self.source == "上市" else 27.0
            remaining_a = limit_rate_a - sum_roc
            price_limit_a = price_t * (1 + remaining_a/100) # Base: Today (T) for Next Day (T+1)
            
            # Rule B Check (Define before use)
            limit_rate_b = 10.0
            remaining_b = limit_rate_b - sum_roc
            price_limit_b = price_t * (1 + remaining_b/100) # Base: Today (T) for Next Day (T+1)
            
            is_fall = (price_t < price_t_1)
            cond_b = (price_t < price_limit_b) or is_fall
            
            # Formatting Output (User Requested No Results, Just Conditions)
            
            lines.append(f"[第二款]") # User requested Header
            
            # Rule A
            hist_a = "<span style='color: #FF4444;'>是</span>" if has_c1_30 else "否"
            
            lines.append(f"1.")
            lines.append(f"前30日曾發生第一款: {hist_a}")
            lines.append(f"且6日漲幅&lt;{int(limit_rate_a)}%  股價 &lt; {price_limit_a:.2f} 則排除")
            
            # Rule B
            hist_b = "<span style='color: #FF4444;'>是</span>" if has_c2_disp_60 else "否"
            
            lines.append(f"<br>2.")
            lines.append(f"前60日曾因第二款進處置: {hist_b}")
            lines.append(f"且6日漲幅&lt;10%  股價 &lt; {price_limit_b:.2f} 則排除")
            lines.append(f"或股價下跌則排除")
            
            return lines
            
        except Exception as e:
            print(f"Exclusion Check Error: {e}")
            return [f"排除計算錯誤: {e}"]