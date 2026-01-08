
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTableWidget, 
                             QTableWidgetItem, QHeaderView, QLabel, QFrame, QTextEdit, 
                             QGridLayout, QScrollArea, QAbstractItemView, QMessageBox,
                             QPushButton, QCalendarWidget, QDialog)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QDate
from PyQt6.QtGui import QColor, QBrush, QFont
from datetime import datetime
import datetime as dt # Alienate to avoid collision
import pandas as pd
from core.utils import DateUtils, ClauseParser
from core.fetcher import StockFetcher
from core.parser import StockParser
from core.cache import CacheManager
from core.predictor import DispositionPredictor

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
                
                # Save to Cache (Only if data found or not latest to avoid caching empty pending data)
                if daily_items:
                    self.cache_mgr.save_daily_data(day_str, daily_items)
                elif not is_latest:
                     # If not latest and empty, maybe it's a holiday or really empty? Save to avoid repeated fetch.
                     self.cache_mgr.save_daily_data(day_str, daily_items)
                else:
                     print(f"DEBUG: Skipping cache save for {day_str} (Latest & Empty)", flush=True)
                
                QThread.msleep(500)
            
            print(f"DEBUG: Day {day_str} Items: {len(daily_items)}", flush=True)
            
            for item in daily_items:
                code = item.get('code', '').strip()
                if not code: continue
                
                name = item.get('name', '')
                source = "上市" if item.get('source', '') == 'TWSE' else "上櫃"
                raw_reason = str(item.get('reason', ''))
                clause = ClauseParser.parse_clauses(raw_reason)
                
                # if code == '2408':
                #     print(f"DEBUG: 2408 found in {day_str}. Clause: {clause}, Raw: {raw_reason[:20]}...", flush=True)
                
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
                
            # Log all codes found for this day
            found_codes_list = [item.get('code','').strip() for item in daily_items]
            print(f"DEBUG: Codes found in {day_str}: {found_codes_list}", flush=True)
        
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
    status_message_updated = pyqtSignal(str) # New Signal

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
        
        # --- Navigation Bar (NEW) ---
        nav_layout = QHBoxLayout()
        nav_layout.addStretch()
        
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
        
        # Prev Button
        self.btn_prev = QPushButton("<")
        self.btn_prev.setFixedSize(40, 40)
        self.btn_prev.setStyleSheet(btn_style)
        self.btn_prev.clicked.connect(lambda: self.change_date(-1))
        nav_layout.addWidget(self.btn_prev)
        
        # Date Label / Button
        self.date_btn = QPushButton("2026/01/02 ▼")
        self.date_btn.setStyleSheet("""
            QPushButton {
                background-color: #1E1E1E;
                color: #FFFFFF;
                border: none;
                font-size: 24px;
                font-weight: bold;
                padding: 5px;
            }
            QPushButton:hover { color: #4da6ff; }
        """)
        self.date_btn.clicked.connect(self.toggle_calendar)
        nav_layout.addWidget(self.date_btn)
        
        # Next Button
        self.btn_next = QPushButton(">")
        self.btn_next.setFixedSize(40, 40)
        self.btn_next.setStyleSheet(btn_style)
        self.btn_next.clicked.connect(lambda: self.change_date(1))
        nav_layout.addWidget(self.btn_next)
        
        nav_layout.addStretch()
        left_layout.addLayout(nav_layout)
        
        # Initial Date State
        self.current_display_date = datetime.now()
        self.cache_manager = CacheManager()
        self.calc_cache = {} # Init Calculation Cache
        
        # TOP AREA
        top_area = QHBoxLayout()
        self.observer_box = InfoBox("觀察區", {}, "#4da6ff")
        self.observer_box.item_clicked.connect(self.highlight_stock)
        top_area.addWidget(self.observer_box, 1)
        self.disposition_box = InfoBox("盤後處置公告", {}, "#FFCC00")
        self.disposition_box.item_clicked.connect(self.highlight_stock)
        top_area.addWidget(self.disposition_box, 1)

        self.disp_exit_box = InfoBox("處置出關區", {}, "#FF4444")
        self.disp_exit_box.item_clicked.connect(self.highlight_stock)
        top_area.addWidget(self.disp_exit_box, 1)
        
        left_layout.addLayout(top_area, 1)
        
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
        # Right Panel Layout
        self.right_heading = QLabel("進處置條件 (點擊股票):")
        self.right_heading.setStyleSheet("font-size: 20px; font-weight: bold; color: #FF4444; margin-bottom: 5px;")
        right_layout.addWidget(self.right_heading)
        
        self.condition_lbl = QLabel("")
        self.condition_lbl.setObjectName("condition_lbl")
        self.condition_lbl.setWordWrap(True)
        self.condition_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.condition_lbl.setStyleSheet("font-size: 16px; color: #DDDDDD; margin-top: 5px; font-weight: bold;")
        right_layout.addWidget(self.condition_lbl, 1) # Expand
        
        # MOVED STATUS TO MAIN WINDOW STATUS BAR
        # We can keep this label for other info or just empty
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
            self.calc_worker = CalculationWorker(code, source)
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
            self.condition_lbl.setText(full_html)
            
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

        headers = ["股票", "名稱", "類別", "處置頻率", "融券", "期貨"]
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

    def start_worker(self):
        self.status_message_updated.emit("正在下載歷史資料...") # Emit Signal
        self.worker = HistoryWorker([]) 
        self.worker.progress_update.connect(self.update_status)
        self.worker.data_ready.connect(self.on_data_ready)
        self.worker.start()
        
    def update_status(self, msg):
        # Emit signal instead of setting local label
        self.status_message_updated.emit(msg)
        
    def on_data_ready(self, agg_data):
        self.status_message_updated.emit("資料載入完成")
        self.populate_table(agg_data)

    def populate_table(self, agg_data):
        self.grid_table.setSortingEnabled(False)
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
             curr = curr - dt.timedelta(days=1)
             
        # --- Dynamic Holiday Detection ---
        valid_dates = set()
        for code, info in agg_data.items():
            clauses = info.get("clauses", {})
            for date_str, clause_val in clauses.items():
                if clause_val: 
                    valid_dates.add(date_str)
                    
        # Filter date_cols
        # Always include Current Date (e.g. 1/5) even if no data yet
        current_date_str = self.calendar["current"]
        date_cols = [d for d in raw_date_cols if d in valid_dates or d == current_date_str]
        
        # Filter pred_history_dates (Must keep order)
        pred_history_dates = [d for d in raw_pred_history_dates if d in valid_dates or d == current_date_str]
        
        if not date_cols: date_cols = raw_date_cols
        if not pred_history_dates: pred_history_dates = raw_pred_history_dates

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
            
            if code == "2413" or code == "1528":
                 print(f"DEBUG {code} RAW: Period='{period}' Start='{disp_start_dt}' End='{disp_end_dt}'", flush=True)
            
            # if code == "2408":
            #     print(f"DEBUG 2408 Clauses: {data.get('clauses', {})}", flush=True)
            
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
                    d_dt = datetime(eff_year, d_month, d_day)
                except Exception as e:
                    d_dt = None

                c_str = clauses_map.get(d, "")
                
                # If currently disposed, ignore clauses BEFORE disposition start
                should_reset = False
                if disp_start_dt and disp_start_dt.date() <= today_dt.date():
                    if d_dt and d_dt.date() <= disp_start_dt.date(): # Inclusive Reset
                        should_reset = True
                        
                if should_reset:
                    c_str = "" # Reset
                


                is_c1 = "一" in c_str
                is_any = len(c_str) > 0
                hist_items.append({"is_clause1": is_c1, "is_any": is_any})
                
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
                    if data["source"] == "上市": listening_twse.append(code_name)
                    else: listening_tpex.append(code_name)
                elif min_needed == 2:
                    if data["source"] == "上市": one_step_twse.append(code_name)
                    else: one_step_tpex.append(code_name)
            
            # --- New Disposition Override ---
            if data.get("is_disposed", False) and disp_start_dt:
                 # Only override message for FUTURE/NEWLY ANNOUNCED (Start > Today)
                 if disp_start_dt.date() > today_dt.date():
                     prob = 100
                     if not warning_msg or "此後" not in warning_msg:
                         warning_msg = f"已進入處置 (生效日: {disp_start_dt.strftime('%m/%d')})"

                 if disp_start_dt > today_dt:
                     suffix = "(期)" if data.get("has_futures") else ""
                     item_str = f"<a href='{code}' style='color: #E0E0E0; text-decoration: none;'>{code}{suffix}&nbsp;{name}</a>"
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
                     item_str = f"<a href='{code}' style='color: #E0E0E0; text-decoration: none;'>{code}{suffix}&nbsp;{name}</a>"
                     if data["source"] == "上市": exit_data[target_idx]["twse"].append(item_str)
                     else: exit_data[target_idx]["tpex"].append(item_str)

            has_visible_clause = False
            for d in date_cols:
                if clauses_map.get(d, ""):
                    has_visible_clause = True
                    break
            
            # If No Warning AND No Visible Clauses -> Skip (Noise)
            if not warning_msg and not has_visible_clause and not data.get("is_disposed", False):
                continue
                
            final_rows.append((code, data, warning_msg, prob, min_needed))

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
        
        for row, (code, data, warning_msg, prob, min_needed) in enumerate(final_rows):
            is_disposed = data.get("is_disposed", False)
            
            # Manual Alternating Background
            # Manual Alternating Background
            base_bg = QColor("#2A2A2A") if row % 2 == 1 else QColor("#1E1E1E")
            
            period = data.get("period", "")
            measure = data.get("measure", "")
            tooltip_txt = f"處置期間: {period}\n處置措施: {measure}" if is_disposed else ""
            
            # Determine Row Highlight: 
            # 1. Yellow ONLY if Announced (Future Start)
            # 2. Pink if Disposed
            # 3. Light Blue if Listening (min_needed == 1) - User Request
            
            is_future_disp = False
            is_listening = False

            if is_disposed:
                disp_start = DateUtils.parse_period_start(period)
                if disp_start:
                     # Calculate today approx or reuse
                     # Ideally use exact today_dt from above.
                     anchor_d = self.calendar["anchor_obj"]
                     t_dt = dt.date(anchor_d.year, anchor_d.month, anchor_d.day)
                     if disp_start.date() > t_dt:
                         is_future_disp = True
            elif min_needed == 1:
                is_listening = True

            # Code
            code_item = QTableWidgetItem(code)
            if is_future_disp:
                 code_item.setBackground(QBrush(QColor("#FFFFAA"))) # Light Yellow
                 code_item.setForeground(QBrush(QColor("#000000"))) 
                 code_item.setToolTip(tooltip_txt)
            elif is_disposed:
                 code_item.setBackground(QBrush(QColor("#FFCCCC"))) # Pink
                 code_item.setForeground(QBrush(QColor("#000000")))
                 code_item.setToolTip(tooltip_txt)
            elif is_listening:
                 code_item.setBackground(QBrush(QColor("#ADD8E6"))) # Light Blue
                 code_item.setForeground(QBrush(QColor("#000000"))) # Black Text
            else:
                 code_item.setBackground(QBrush(base_bg))
                 code_item.setForeground(QBrush(QColor("#CCCCCC")))
                 if is_disposed: code_item.setToolTip(tooltip_txt)

            self.grid_table.setItem(row, 0, code_item)

            # Name
            name_item = QTableWidgetItem(data["name"])
            if is_future_disp:
                 name_item.setBackground(QBrush(QColor("#FFFFAA")))
                 name_item.setForeground(QBrush(QColor("#000000")))
                 name_item.setToolTip(tooltip_txt)
            elif is_disposed:
                 name_item.setBackground(QBrush(QColor("#FFCCCC"))) # Pink
                 name_item.setForeground(QBrush(QColor("#000000")))
                 name_item.setToolTip(tooltip_txt)
            elif is_listening:
                 name_item.setBackground(QBrush(QColor("#ADD8E6"))) # Light Blue
                 name_item.setForeground(QBrush(QColor("#000000"))) # Black Text
            else:
                 name_item.setBackground(QBrush(base_bg))
                 name_item.setForeground(QBrush(QColor("#CCCCCC")))
                 if is_disposed: name_item.setToolTip(tooltip_txt)
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
                if "六十分鐘" in measure or "60分鐘" in measure: freq_text = "60分"
                elif "四十五分鐘" in measure or "45分鐘" in measure: freq_text = "45分"
                elif "二十五分鐘" in measure or "25分鐘" in measure: freq_text = "25分"
                elif "二十分鐘" in measure or "20分鐘" in measure: freq_text = "20分"
                elif "十分鐘" in measure or "10分鐘" in measure: freq_text = "10分"
                elif "五分鐘" in measure or "5分鐘" in measure: freq_text = "5分"
            
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
            pred_lbl = QLabel(warning_msg)
            pred_lbl.setWordWrap(True)
            pred_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
             
            if "已達" in warning_msg or "已進入處置" in warning_msg:
                # Highlight "Reached" or "Entered Disposition"
                pred_lbl.setStyleSheet("QLabel { border: none; background-color: #FFFFCC; color: #000000; font-size: 12px; font-weight: bold; }")
            else:
                # Normal Warning
                pred_lbl.setStyleSheet(f"QLabel {{ border: none; background-color: {bg_hex}; color: #FFAAAA; font-size: 12px; }}")
            
            # Sort Item for Prediction
            pred_item = QTableWidgetItem(warning_msg)
            self.grid_table.setItem(row, col_idx, pred_item)
                
            self.grid_table.setCellWidget(row, col_idx, pred_lbl)
            
            # Original ID (Hidden)
            col_idx += 1
            orig_item = SortableWidgetItem()
            orig_item.setData(Qt.ItemDataRole.UserRole, row) # UserRole
            orig_item.setData(Qt.ItemDataRole.DisplayRole, row)
            self.grid_table.setItem(row, col_idx, orig_item)
            
            self.grid_table.setRowHeight(row, 60 if warning_msg else 30)

        # self.grid_table.setSortingEnabled(True) # DIY Sorting
        


        # Update Observer Box
        # Update Observer Box
        obs_list = [
            ("聽牌", ""),
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(listening_twse) if listening_twse else "無"),
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(listening_tpex) if listening_tpex else "無"),
            ("一進聽", ""),
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(one_step_twse) if one_step_twse else "無"),
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(one_step_tpex) if one_step_tpex else "無")
        ]
        self.observer_box.update_items(obs_list)
        
        # Update Disposition Notice Box
        notice_list = [
            ("上市", ""),
            ("     ", "&nbsp;&nbsp;&nbsp;&nbsp;".join(notice_twse) if notice_twse else "無"),
            ("上櫃", ""),
            ("     ", "&nbsp;&nbsp;&nbsp;&nbsp;".join(notice_tpex) if notice_tpex else "無")
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
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[0]["twse"]) if exit_data[0]["twse"] else "無"),
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[0]["tpex"]) if exit_data[0]["tpex"] else "無"),
            (lbl_2, ""),
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[1]["twse"]) if exit_data[1]["twse"] else "無"),
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[1]["tpex"]) if exit_data[1]["tpex"] else "無"),
            (lbl_3, ""),
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[2]["twse"]) if exit_data[2]["twse"] else "無"),
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(exit_data[2]["tpex"]) if exit_data[2]["tpex"] else "無")
        ]
        if hasattr(self, "disp_exit_box"):
            self.disp_exit_box.update_items(exit_list)
        else:
             print("CRITICAL ERROR: disp_exit_box not found!", flush=True)

        # SAVE SUMMARY TO DB (Only if it's the latest/fetched data)
        # We assume populate_table is used for the active computation
        date_str = self.current_display_date.strftime("%Y%m%d")
        summary_data = {
            "obs": obs_list,
            "notice": notice_list,
            "exit": exit_list
        }
        self.cache_manager.save_dashboard_summary(date_str, summary_data)

    def change_date(self, offset):
        new_date = self.current_display_date + dt.timedelta(days=offset)
        self.load_data_for_date(new_date)
        
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

        self.grid_table.cellDoubleClicked.connect(self.on_table_double_clicked)
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

    def load_data_for_date(self, target_date):
        try:
            self.current_display_date = target_date
            # Clear Cache on Date Change
            self.calc_cache.clear()
            
            date_str = target_date.strftime("%Y%m%d")
            display_str = target_date.strftime("%Y/%m/%d")
            self.date_btn.setText(f"{display_str} ▼")
            
            # 1. Try Load Summary (Fast, for top 3 blocks)
            summary = self.cache_manager.get_dashboard_summary(date_str)
            if summary:
                self.observer_box.update_items(summary.get("obs", {}))
                self.disposition_box.update_items(summary.get("notice", {}))
                if hasattr(self, "disp_exit_box"):
                    self.disp_exit_box.update_items(summary.get("exit", {}))
            else:
                # Clear or show empty
                self.observer_box.update_items({})
                self.disposition_box.update_items({})
                if hasattr(self, "disp_exit_box"): self.disp_exit_box.update_items({})
                
            # NOTE: User requested table to NOT change when navigating history. 
            # Only top 3 blocks change. Table shows latest data.
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

# --- Worker ---
from core.fetcher import StockFetcher
from core.calculator import DispositionCalculator
from core.predictor import DispositionPredictor
from core.cache import CacheManager
from core.utils import DateUtils

class CalculationWorker(QThread):
    result_ready = pyqtSignal(tuple) # (calc_lines, excl_lines)
    
    def __init__(self, code, source):
        super().__init__()
        self.code = code
        self.source = source
        
    def run(self):
        fetcher = StockFetcher()
        # Fetch 180 days for calculation (Need 60-90 days ref)
        df, shares = fetcher.fetch_stock_history(self.code, self.source, period="180d")
        
        if df is None or df.empty:
            self.result_ready.emit((self.code, (["無法取得歷史股價"], []))) # Fix tuple unpacking
            return
            
        # [Fix] Apply 18:00 cutoff logic
        # Truncate dataframe to ensure we don't peek into "Today" if it's before 18:00
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
             today = dt.datetime.now()
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
                 hist_att = fetcher.fetch_stock_attention_history(self.code, s_date, e_date, self.source)
                 if hist_att:
                     rows = []
                     if isinstance(hist_att, dict):
                         if 'data' in hist_att: rows = hist_att['data'] # TWSE
                         elif 'tables' in hist_att and len(hist_att['tables'])>0: rows = hist_att['tables'][0].get('data', []) # TPEX
                     elif isinstance(hist_att, list): rows = hist_att
                     
                     for r in rows:
                         found_date = ""
                         if isinstance(r, list) and len(r)>1: found_date = str(r[1])
                         elif isinstance(r, dict): found_date = str(r.get("Date", ""))
                         
                         ad_date = ""
                         if "/" in found_date:
                             ps = found_date.split('/')
                             if len(ps)==3: ad_date = f"{int(ps[0])+1911}{ps[1]}{ps[2]}"
                         
                         if ad_date and ad_date >= cutoff_30:
                             r_str = str(r)
                             if "第一款" in r_str or "第1款" in r_str or ("款" in r_str and "一" in r_str):
                                 has_c1_30 = True
                                 break
                                 
                 # 2. Disposition (Rule B)
                 hist_disp = fetcher.fetch_stock_disposition_history(self.code, s_date, e_date, self.source)
                 if hist_disp:
                     rows_d = []
                     if isinstance(hist_disp, dict) and 'data' in hist_disp: rows_d = hist_disp['data']
                     elif isinstance(hist_disp, list): rows_d = hist_disp
                     
                     for r in rows_d:
                         found_date = ""
                         if isinstance(r, list) and len(r)>1: found_date = str(r[1])
                         elif isinstance(r, dict): found_date = str(r.get("Date", ""))
                         
                         ad_date = ""
                         if "/" in found_date:
                             ps = found_date.split('/')
                             if len(ps)==3: ad_date = f"{int(ps[0])+1911}{ps[1]}{ps[2]}"
                             
                         if ad_date and ad_date >= cutoff_60:
                             r_str = str(r)
                             if "第二款" in r_str or "第2款" in r_str or ("款" in r_str and "二" in r_str):
                                 has_c2_disp_60 = True
                                 break
             except Exception as e:
                 print(f"Live Check Error: {e}")
             
             # --- End Live Check ---

             
             # Look back 60 days (Extended for Rule B)
             for i in range(60, 0, -1):
                 d = today - dt.timedelta(days=i)
                 d_str = d.strftime("%Y%m%d")
                 data = cache.get_daily_data(d_str)
                 
                 found_for_day = False
                 if data:
                     target = next((x for x in data if x.get("code") == self.code), None)
                     if target:
                         # Parse Reason from Cache (Fix for missing boolean flags)
                         raw_reason = str(target.get("reason", ""))
                         c_str = ClauseParser.parse_clauses(raw_reason)
                         
                         is_c1 = ("一" in c_str)
                         is_any = (len(c_str) > 0)
                         
                         # Check 30-day window for Rule A (Clause 1)
                         if i <= 30 and is_c1:
                             has_c1_30 = True
                             
                         # Check 60-day window for Rule B (Disposition via Clause 2)
                         # Logic: Check if "處置" and ("二" or "2") in reason text
                         if "處置" in raw_reason and ("二" in raw_reason or "2" in raw_reason):
                             has_c2_disp_60 = True
                         
                         history_items.append({
                             "is_clause1": is_c1,
                             "is_any": is_any
                         })
                         found_for_day = True
                 
                 # If not found but IS a trading day, append Empty Record (Break Streak)
                 if not found_for_day:
                     if DateUtils.is_trading_day(d):
                         history_items.append({
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
        
        # Calculate
        lines, is_clause2_risk = DispositionCalculator.calculate_conditions(
            df, self.source, shares, needed_c1=needed_c1, needed_any=needed_any
        )
        
        # Calculate Exclusion (Pass History Flags + Clause 2 Risk)
        excl_lines = self.check_exclusion_rules(df, has_c1_30, has_c2_disp_60, is_clause2_risk)
        
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
