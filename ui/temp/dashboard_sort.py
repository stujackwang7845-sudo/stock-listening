
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTableWidget, 
                             QTableWidgetItem, QHeaderView, QLabel, QFrame, QTextEdit, 
                             QGridLayout, QScrollArea, QAbstractItemView, QMessageBox)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont
from datetime import datetime
import datetime # explicit import for timedelta
from core.utils import DateUtils, ClauseParser
from core.fetcher import StockFetcher
from core.parser import StockParser
from core.cache import CacheManager
from core.predictor import DispositionPredictor

class InfoBox(QFrame):
    def __init__(self, title, items_dict=None, color_theme="#4da6ff"):
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
        
        title_lbl = QLabel(title)
        title_lbl.setObjectName("title")
        layout.addWidget(title_lbl)
        
        self.content_layout = QVBoxLayout()
        layout.addLayout(self.content_layout)
        
        if items_dict:
            self.update_items(items_dict)
            
    def update_items(self, items_data):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            
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
                row.addWidget(val_lbl, 1) # Expand
                
            self.content_layout.addLayout(row)
        self.content_layout.addStretch()

class HistoryWorker(QThread):
    data_ready = pyqtSignal(dict) # aggregated data
    progress_update = pyqtSignal(str)
    
    def __init__(self, dates_to_fetch):
        super().__init__()
        self.dates_to_fetch = dates_to_fetch 
        self.cache_mgr = CacheManager()

    def run(self):
        fetcher = StockFetcher()
        parser = StockParser()
        
        agg_data = {}
        
        # 0. Fetch Margin/Short Lists
        self.progress_update.emit("正在抓取融券/信用交易名單...")
        margin_codes = set()
        
        last_trading_day = DateUtils.get_last_trading_day()
        margin_date_str = last_trading_day.strftime("%Y%m%d")
        
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
        self.progress_update.emit("正在抓取今日處置股名單...")
        disposition_map = {} # code -> {name, source}
        
        # TWSE Disposition
        twse_disp = fetcher.fetch_twse_disposition(None)
        if twse_disp:
            parsed = parser.parse_twse_disposition(twse_disp)
            print(f"DEBUG: Parsed TWSE Disposition Items: {len(parsed)}")
            for item in parsed:
                code = item["code"].strip()
                disposition_map[code] = {
                    "name": item["name"], 
                    "source": "上市",
                    "period": item.get("period", ""),
                    "measure": item.get("measure", "")
                }

        # TPEX Disposition
        tpex_disp = fetcher.fetch_tpex_disposition(margin_date_str)
        if tpex_disp:
            parsed = parser.parse_tpex_disposition(tpex_disp)
            print(f"DEBUG: Parsed TPEX Disposition Items: {len(parsed)}")
            for item in parsed:
                code = item["code"].strip()
                disposition_map[code] = {
                    "name": item["name"], 
                    "source": "上櫃",
                    "period": item.get("period", ""),
                    "measure": item.get("measure", "")
                }
        
        print(f"DEBUG: Final Disposition Map Keys: {list(disposition_map.keys())}") 
        
        # Determine Fetch Dates (10 days history for prediction)
        last_trading_day = DateUtils.get_last_trading_day()
        
        days_to_fetch = []
        days_to_fetch.append(last_trading_day)
        
        count = 0
        curr = last_trading_day
        while count < 10:
            curr = curr - datetime.timedelta(days=1)
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
                
                # Save to Cache
                self.cache_mgr.save_daily_data(day_str, daily_items)
                
                QThread.msleep(500)
            
            for item in daily_items:
                code = item.get('code', '').strip()
                if not code: continue
                
                name = item.get('name', '')
                source = "上市" if item.get('source', '') == 'TWSE' else "上櫃"
                raw_reason = str(item.get('reason', ''))
                clause = ClauseParser.parse_clauses(raw_reason)
                
                if code not in agg_data:
                    agg_data[code] = {
                        "name": name,
                        "source": source,
                        "can_short": (code in margin_codes),
                        "has_futures": (code in futures_codes),
                        "clauses": {},
                        "is_disposed": False 
                    }
                
                # Update Clause
                agg_data[code]["clauses"][display_date] = clause
        
        # --- Post-Processing: Apply Disposition Status & Add Missing Disposed Stocks ---
        for code, info in disposition_map.items():
            if code in agg_data:
                agg_data[code]["is_disposed"] = True
                agg_data[code]["period"] = info["period"]
                agg_data[code]["measure"] = info["measure"]
            else:
                # Add missing disposed stock
                agg_data[code] = {
                    "name": info["name"],
                    "source": info["source"],
                    "can_short": (code in margin_codes),
                    "has_futures": (code in futures_codes),
                    "clauses": {}, # No clauses history
                    "is_disposed": True,
                    "period": info["period"],
                    "measure": info["measure"]
                }

            
        self.data_ready.emit(agg_data)


class Dashboard(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()
        QTimer.singleShot(500, self.start_worker)
        
    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # LEFT SIDE
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0,0,0,0)
        
        # TOP AREA
        top_area = QHBoxLayout()
        self.observer_box = InfoBox("觀察區", {}, "#4da6ff")
        top_area.addWidget(self.observer_box, 1)
        self.disposition_box = InfoBox("盤後處置公告", {}, "#FFCC00")
        top_area.addWidget(self.disposition_box, 1)

        self.disp_exit_box = InfoBox("處置區", {}, "#FF4444")
        top_area.addWidget(self.disp_exit_box, 1)
        
        left_layout.addLayout(top_area, 1)
        
        # MIDDLE TABLE
        self.grid_table = QTableWidget()
        self.grid_table.verticalHeader().setVisible(False)
        self.grid_table.setAlternatingRowColors(False) # Disable auto-alternating
        self.grid_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.grid_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        
        self.update_headers()
        left_layout.addWidget(self.grid_table, 3)
        main_layout.addWidget(left_container, 4)
        
        # RIGHT PANEL
        right_panel = QFrame()
        right_panel.setStyleSheet("QFrame { border: 2px solid #4da6ff; border-radius: 4px; background-color: #1E1E1E; } QLabel { border: none; font-size: 14px; color: #CCC; }")
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(QLabel("進處置條件:"))
        right_layout.addWidget(QLabel("資料載入中...", objectName="status_lbl"))
        right_layout.addStretch()
        main_layout.addWidget(right_panel, 1)
        
        self.status_lbl = right_panel.findChild(QLabel, "status_lbl")
        
    def update_headers(self):
        last_trading_day = DateUtils.get_last_trading_day()
        # User requested +9 days (up to 1/15 approx)
        # 9 past days to include 12/18
        self.calendar = DateUtils.get_market_calendar(last_trading_day, past_days=9, future_days=9)
        
        headers = ["股票", "名稱", "類別", "融券", "期貨"]
        headers.extend(self.calendar["past"]) # Hist days
        headers.append(f"{self.calendar['current']} (今)") # 1 day
        headers.extend(self.calendar["future"]) # Future days
        headers.append("機率") # Probability Column
        headers.append("處置預測") # Prediction Column (Last)
        
        self.grid_table.setColumnCount(len(headers))
        self.grid_table.setHorizontalHeaderLabels(headers)
        self.grid_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def start_worker(self):
        self.status_lbl.setText("正在下載歷史資料...")
        self.worker = HistoryWorker([]) 
        self.worker.progress_update.connect(self.update_status)
        self.worker.data_ready.connect(self.on_data_ready)
        self.worker.start()
        
    def update_status(self, msg):
        self.status_lbl.setText(msg)
        
    def on_data_ready(self, agg_data):
        self.status_lbl.setText("資料載入完成")
        self.populate_table(agg_data)

    def populate_table(self, agg_data):
        self.grid_table.setRowCount(0)
        
        # Identify date columns (Initial Guess)
        raw_date_cols = self.calendar["past"] + [self.calendar["current"]]
        
        # Prepare prediction dates (Initial Guess)
        raw_pred_history_dates = []
        last_trading_day = DateUtils.get_last_trading_day()
        curr = last_trading_day
        count = 0
        while count < 10:
             if DateUtils.is_trading_day(curr):
                  raw_pred_history_dates.insert(0, curr.strftime("%m/%d"))
                  count += 1
             curr = curr - datetime.timedelta(days=1)
             
        # --- Dynamic Holiday Detection ---
        valid_dates = set()
        for code, info in agg_data.items():
            clauses = info.get("clauses", {})
            for date_str, clause_val in clauses.items():
                if clause_val: 
                    valid_dates.add(date_str)
                    
        # Filter date_cols
        date_cols = [d for d in raw_date_cols if d in valid_dates]
        # Filter pred_history_dates (Must keep order)
        pred_history_dates = [d for d in raw_pred_history_dates if d in valid_dates]
        
        if not date_cols: date_cols = raw_date_cols
        if not pred_history_dates: pred_history_dates = raw_pred_history_dates

        # Sort by code
        valid_keys = [str(k) for k in agg_data.keys() if isinstance(k, str) or isinstance(k, int)]
        sorted_codes = sorted(valid_keys)
        
        final_rows = [] 
        
        anchor_year = self.calendar["anchor_obj"].year
        anchor_month = self.calendar["anchor_obj"].month
        anchor_date = self.calendar["anchor_obj"]
        today_dt = datetime.datetime(anchor_date.year, anchor_date.month, anchor_date.day)
        
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
                 future_dts.append(datetime.datetime(y, m, d))
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
        
        for code in sorted_codes:
            data = agg_data[code]
            name = data["name"]
            
            # 1. Filter Warrants
            if "購" in name or "售" in name: continue
            # 2. Filter DR
            if "DR" in name: continue
            
            # Calculate Prediction
            # Parse Disposition Start/End Date if available
            period = data.get("period", "")
            disp_start_dt = DateUtils.parse_period_start(period)
            disp_end_dt = DateUtils.parse_period_end(period)
            
            if code == "2413":
                 # print(f"DEBUG 2413 RAW: Period='{period}' Start='{disp_start_dt}' End='{disp_end_dt}'", flush=True)
                 pass
            
            # Debug (Temporary: Remove later)
            if code == "3354":
                 print(f"DEBUG 3354: Period='{period}' Start='{disp_start_dt}'", flush=True)
                 print(f"  Hist Dates: {pred_history_dates}", flush=True)

            hist_items = []
            clauses_map = data["clauses"]
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
                        
                    d_dt = datetime.datetime(eff_year, d_month, d_day)
                except:
                    d_dt = None

                c_str = clauses_map.get(d, "")
                
                if code == "3354":
                    print(f"  Date {d} -> {d_dt}. Raw Clause: '{c_str}'", flush=True)

                # If currently disposed, ignore clauses BEFORE disposition start
                # ONLY if disposition has already started in the past.
                # If it starts Today or Future, we want to SEE the history that caused it.
                should_reset = False
                if disp_start_dt and disp_start_dt < today_dt:
                    if d_dt and d_dt < disp_start_dt:
                        should_reset = True
                        
                if should_reset:
                    c_str = "" # Reset
                    # Debug
                    if code == "3354":
                        print(f"    -> RESET (Strictly Before {disp_start_dt})", flush=True)
                
                is_c1 = "一" in c_str
                is_any = len(c_str) > 0
                hist_items.append({"is_clause1": is_c1, "is_any": is_any})
                
            # Limit prediction to next 3 days only (Ignore 4+ days)
            warning_msg, prob, min_needed = DispositionPredictor.analyze(hist_items, future_days=3)
            
            # Collect Observer Data (Only for non-disposed)
            if not data.get("is_disposed", False) and not disp_start_dt:
                code_name = f"{code}\u00A0{name}"
                if min_needed == 1:
                    if data["source"] == "上市": listening_twse.append(code_name)
                    else: listening_tpex.append(code_name)
                elif min_needed == 2:
                    if data["source"] == "上市": one_step_twse.append(code_name)
                    else: one_step_tpex.append(code_name)
            
            # --- New Disposition Override ---
            # If stock is disposed AND Start Date >= Today (Newly Disposed), force High Priority (Top)
            # If Start Date < Today (Old Disposed), reset logic above clears history -> Low Priority (Bottom)
            if data.get("is_disposed", False) and disp_start_dt:
                 if disp_start_dt >= today_dt:
                     prob = 100
                     if not warning_msg:
                         warning_msg = f"已進入處置 (生效日: {disp_start_dt.strftime('%m/%d')})"
                         
                     # Add to Notice Box if Future Start OR Today Start (Announced 12/31 or 12/30 effective today)
                     if code == "2413":
                         print(f"DEBUG 2413: Period='{period}' Start='{disp_start_dt}' Today='{today_dt}'", flush=True)
                         print(f"  Is Disposed: {data.get('is_disposed')} Source: {data.get('source')}", flush=True)

                     if disp_start_dt >= today_dt:
                         suffix = "(期)" if data.get("has_futures") else ""
                         item_str = f"{code}{suffix}\u00A0{name}"
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
                     suffix = "(期)" if data.get("has_futures") else ""
                     item_str = f"{code}{suffix}\u00A0{name}"
                     if data["source"] == "上市": exit_data[target_idx]["twse"].append(item_str)
                     else: exit_data[target_idx]["tpex"].append(item_str)
            
            if code == "3354":
                print(f"  Final Warning: {warning_msg}, Prob: {prob}", flush=True)
                print(f"  Hist Items: {hist_items}", flush=True)
            
            # Check for visible clauses
            has_visible_clause = False
            for d in date_cols:
                if clauses_map.get(d, ""):
                    has_visible_clause = True
                    break
            
            # If No Warning AND No Visible Clauses -> Skip (Noise)
            if not warning_msg and not has_visible_clause and not data.get("is_disposed", False):
                continue
                
            final_rows.append((code, data, warning_msg, prob))

        # Update Headers (simplified)
        headers = ["股票", "名稱", "類別", "處置頻率", "融券", "期貨"]
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
        
        self.grid_table.setColumnCount(len(headers))
        self.grid_table.setHorizontalHeaderLabels(headers)
        self.grid_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        
        # Sort by Probability (Desc), then Code (Asc)
        final_rows.sort(key=lambda x: (-x[3], x[0]))
        
        self.grid_table.setRowCount(len(final_rows))
        
        for row, (code, data, warning_msg, prob) in enumerate(final_rows):
            is_disposed = data.get("is_disposed", False)
            
            # Manual Alternating Background
            # Manual Alternating Background
            base_bg = QColor("#2A2A2A") if row % 2 == 1 else QColor("#1E1E1E")
            
            period = data.get("period", "")
            measure = data.get("measure", "")
            tooltip_txt = f"處置期間: {period}\n處置措施: {measure}" if is_disposed else ""
            
            # Code
            code_item = QTableWidgetItem(code)
            if is_disposed:
                 code_item.setBackground(QBrush(QColor("#FFCCCC")))
                 code_item.setForeground(QBrush(QColor("#000000"))) 
                 code_item.setToolTip(tooltip_txt)
            else:
                 code_item.setBackground(QBrush(base_bg))
                 code_item.setForeground(QBrush(QColor("#CCCCCC")))
            self.grid_table.setItem(row, 0, code_item)

            # Name
            name_item = QTableWidgetItem(data["name"])
            if is_disposed:
                 name_item.setBackground(QBrush(QColor("#FFCCCC")))
                 name_item.setForeground(QBrush(QColor("#000000")))
                 name_item.setToolTip(tooltip_txt)
            else:
                 name_item.setBackground(QBrush(base_bg))
                 name_item.setForeground(QBrush(QColor("#CCCCCC")))
            self.grid_table.setItem(row, 1, name_item)
            
            # Source
            source_item = QTableWidgetItem(data["source"])
            source_item.setBackground(QBrush(base_bg))
            if data["source"] == "上市":
                source_item.setForeground(QBrush(QColor("#44FF44")))
            else:
                source_item.setForeground(QBrush(QColor("#FFCC00")))
            self.grid_table.setItem(row, 2, source_item)
            
            # Disposition Frequency (Col 3) - NEW
            freq_text = ""
            if is_disposed:
                if "五分鐘" in measure or "5分鐘" in measure: freq_text = "5分"
                elif "十分鐘" in measure or "10分鐘" in measure: freq_text = "10分"
                elif "二十分鐘" in measure or "20分鐘" in measure: freq_text = "20分"
                elif "二十五分鐘" in measure or "25分鐘" in measure: freq_text = "25分"
                elif "四十五分鐘" in measure or "45分鐘" in measure: freq_text = "45分"
                elif "六十分鐘" in measure or "60分鐘" in measure: freq_text = "60分"
            
            freq_item = QTableWidgetItem(freq_text)
            freq_item.setBackground(QBrush(base_bg))
            if freq_text:
                freq_item.setForeground(QBrush(QColor("#FFCC00"))) # Gold
                freq_item.setToolTip(measure) # Show full measure on hover
            else:
                freq_item.setForeground(QBrush(QColor("#CCCCCC")))
            freq_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid_table.setItem(row, 3, freq_item)
            
            # Short Selling Status (Col 4)
            can_short = data.get("can_short", False)
            short_item = QTableWidgetItem("可" if can_short else "")
            short_item.setBackground(QBrush(base_bg))
            if can_short:
                short_item.setForeground(QBrush(QColor("#44FF44")))
                short_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                 short_item.setForeground(QBrush(QColor("#CCCCCC")))
            self.grid_table.setItem(row, 4, short_item)
            
            # Futures Status (Col 5)
            has_futures = data.get("has_futures", False)
            futures_item = QTableWidgetItem("有" if has_futures else "")
            futures_item.setBackground(QBrush(base_bg))
            if has_futures:
                 futures_item.setForeground(QBrush(QColor("#4da6ff"))) 
                 futures_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                 futures_item.setForeground(QBrush(QColor("#CCCCCC")))
            self.grid_table.setItem(row, 5, futures_item)
            
            # Dates (Restored QLabel for HTML styling)
            # Dates (Restored QLabel for HTML styling)
            col_idx = 6
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
                        if c == "一":
                            html_parts.append(f"<span style='color: #FF4444; font-weight: bold;'>{c}</span>")
                        else:
                            html_parts.append(f"<span style='color: #4da6ff;'>{c}</span>")
                
                final_html = ",".join(html_parts)
                lbl = QLabel(final_html)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                # Set background to match row
                lbl.setStyleSheet(f"QLabel {{ background-color: {bg_hex}; border: none; font-size: 14px; }}")
                
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
            self.grid_table.setCellWidget(row, col_idx, prob_lbl)
            col_idx += 1
            
            # Prediction (Restored QLabel with WordWrap)
            pred_lbl = QLabel(warning_msg)
            pred_lbl.setWordWrap(True)
            pred_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
             
            if "已達" in warning_msg or "已進入處置" in warning_msg:
                # Highlight "Reached" or "Entered Disposition"
                pred_lbl.setStyleSheet("QLabel { border: none; background-color: #FFFFCC; color: #000000; font-size: 12px; font-weight: bold; }")
            else:
                # Normal Warning
                pred_lbl.setStyleSheet(f"QLabel {{ border: none; background-color: {bg_hex}; color: #FFAAAA; font-size: 12px; }}")
                
            self.grid_table.setCellWidget(row, col_idx, pred_lbl)
            self.grid_table.setRowHeight(row, 60 if warning_msg else 30)

        # Update Observer Box
        obs_list = [
            ("聽牌", ""),
            ("     上市", "  ".join(listening_twse) if listening_twse else "無"),
            ("     上櫃", "  ".join(listening_tpex) if listening_tpex else "無"),
            ("一進聽", ""),
            ("     上市", "  ".join(one_step_twse) if one_step_twse else "無"),
            ("     上櫃", "  ".join(one_step_tpex) if one_step_tpex else "無")
        ]
        self.observer_box.update_items(obs_list)
        
        # Update Disposition Notice Box
        notice_list = [
            ("上市", ""),
            ("     ", "  ".join(notice_twse) if notice_twse else "無"),
            ("上櫃", ""),
            ("     ", "  ".join(notice_tpex) if notice_tpex else "無")
        ]

        self.disposition_box.update_items(notice_list)
        
        # Update Exit Box
        # Labels depend on calendar
        # 0 -> Free on future[0] (Tomorrow)
        # 1 -> Free on future[1] (Day After)
        # 2 -> Free on future[2] (3rd Day)
        
        def safe_date(idx):
            if idx < len(self.calendar["future"]): return self.calendar["future"][idx]
            return "??"

        lbl_1 = f"明天({safe_date(0)})出關"
        lbl_2 = f"後天({safe_date(1)})出關"
        lbl_3 = f"大後天({safe_date(2)})出關"
        
        exit_list = [
            (lbl_1, ""),
            ("     上市", "  ".join(exit_data[0]["twse"]) if exit_data[0]["twse"] else "無"),
            ("     上櫃", "  ".join(exit_data[0]["tpex"]) if exit_data[0]["tpex"] else "無"),
            (lbl_2, ""),
            ("     上市", "  ".join(exit_data[1]["twse"]) if exit_data[1]["twse"] else "無"),
            ("     上櫃", "  ".join(exit_data[1]["tpex"]) if exit_data[1]["tpex"] else "無"),
            (lbl_3, ""),
            ("     上市", "  ".join(exit_data[2]["twse"]) if exit_data[2]["twse"] else "無"),
            ("     上櫃", "  ".join(exit_data[2]["tpex"]) if exit_data[2]["tpex"] else "無")
        ]
        if hasattr(self, "disp_exit_box"):
            self.disp_exit_box.update_items(exit_list)
        else:
             print("CRITICAL ERROR: disp_exit_box not found!", flush=True)

