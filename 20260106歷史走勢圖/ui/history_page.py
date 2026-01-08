from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel, 
    QPushButton, QLineEdit, QComboBox, QFrame, QTextEdit, 
    QGridLayout, QSizePolicy, QToolBar, QMenu, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QAction, QIcon
import pandas as pd
from datetime import datetime, timedelta
import matplotlib
import matplotlib.pyplot as plt
matplotlib.use('QtAgg')
plt.rcParams['font.sans-serif'] = ['Microsoft JhengHei'] 
plt.rcParams['axes.unicode_minus'] = False # Fix minus sign
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import mplfinance as mpf
import yfinance as yf
import requests
import numpy as np
import io

from core.history_manager import HistoryManager
from core.utils import DateUtils
from core.cache import CacheManager
import io
import os

class HistoryChartWidget(QWidget):
    def __init__(self, token, stock_id, date_str, title_suffix="", delay_ms=0, cache_manager=None):
        super().__init__()
        self.cache_manager = cache_manager
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.figure = Figure(figsize=(4, 2.5), dpi=80)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.layout.addWidget(self.canvas)
        
        self.token = token
        self.stock_id = stock_id
        self.date_str = date_str
        self.title_suffix = title_suffix
        
        # Show loading text
        self.text_ax = self.figure.add_subplot(111)
        self.text_ax.text(0.5, 0.5, "Loading...", ha='center', va='center', fontsize=9, color='gray')
        self.text_ax.axis('off')
        self.canvas.draw()
        
        QTimer.singleShot(delay_ms, self._fetch_data)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        reload_action = menu.addAction("重新下載資料(Reload)")
        
        action = menu.exec(event.globalPos())
        if action == reload_action:
            self.text_ax.clear()
            self.text_ax.text(0.5, 0.5, "Reloading...", ha='center', va='center', fontsize=9, color='gray')
            self.canvas.draw()
            self._fetch_data(force_refresh=True)

    def _fetch_data(self, force_refresh=False):
        target_date = self.date_str
        end_date = None
        
        # Calculate End Date (Next Day) for yfinance range (start, end)
        try:
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            today = datetime.now()
            today_str = today.strftime("%Y-%m-%d")
            
            # Future/Today Check (User requested NO live data, only history)
            if dt.date() >= today.date():
                self.figure.clear()
                ax = self.figure.add_subplot(111)
                msg = "資料尚未結算" if dt.date() == today.date() else "尚未開盤"
                ax.text(0.5, 0.5, f"{target_date}\n{msg}", ha='center', va='center', fontsize=9, color='gray')
                ax.axis('off')
                self.canvas.draw()
                return

            if target_date != today_str:
                dt_next = dt + timedelta(days=1)
                end_date = dt_next.strftime("%Y-%m-%d")
        except:
            today_str = "" 
            end_date = None
        
        df = None
        loaded_from_cache = False
        needs_refetch = False
        
        # 1. Try Cache (DB)
        if not force_refresh and target_date != today_str and self.cache_manager:
            try:
                json_str = self.cache_manager.get_chart_data(self.stock_id, target_date)
                print(f"DEBUG: Cache Check {self.stock_id} {target_date}: {str(json_str)[:50] if json_str else 'None'}")
                if json_str:
                    temp_df = pd.read_json(io.StringIO(json_str))
                    
                    # Ensure index is DatetimeIndex
                    # [Auto-Fix] Check if historical data is incomplete (before 13:00)
                    needs_refetch = False
                    if target_date != today_str and not temp_df.empty:
                        last_dt = temp_df.index[-1]
                        
                        # Handle Timezone
                        check_time = last_dt
                        if last_dt.tzinfo is not None:
                            try:
                                check_time = last_dt.tz_convert('Asia/Taipei')
                            except:
                                check_time = last_dt + timedelta(hours=8)
                        
                        if check_time.hour < 9:
                             check_time = check_time + timedelta(hours=8)
                        
                        last_time = check_time.time()
                        
                        # Use 13:00 threshold (Illiquid stocks often stop ~13:00-13:25)
                        market_close = datetime.strptime("13:00", "%H:%M").time()
                        
                        if last_time < market_close:
                            needs_refetch = True
                            print(f"DEBUG: Incomplete Cache {self.stock_id}: Last={last_time} < {market_close}. Needs Refetch.")

                    df = temp_df
                    loaded_from_cache = True
                    # If needs refetch, we treat as if NOT fully loaded, but keep df as backup
                    if needs_refetch:
                        loaded_from_cache = False 
            except Exception as e:
                print(f"DB Cache Load Error: {e}")

        # 2. Fetch if needed
        prev_close = None
        curr_price_info = None
        cached_df = df if needs_refetch else None # Backup
        
        if not loaded_from_cache or force_refresh:
            print(f"DEBUG: Fetching YF for {self.stock_id} (Refetch={needs_refetch})")
            def fetch_yf(suffix):
                ticker = f"{self.stock_id}{suffix}"
                p_close = None
                c_price = None
                try:
                    stock = yf.Ticker(ticker)
                    
                    # Try to get info first for accurate Close/PrevClose
                    try:
                       info = stock.info
                       p_close = info.get('previousClose')
                       c_price = info.get('currentPrice') or info.get('regularMarketPrice')
                    except:
                       pass

                    # [Fix] stock.info returns LIVE data (Today's Price/PrevClose). 
                    # For historical charts (Past Dates), we must NOT use this.
                    # Otherwise we show Today's price on Yesterday's chart.
                    if target_date != today_str:
                        p_close = None
                        c_price = None

                    if target_date == today_str:
                        d = stock.history(interval="1m", period="1d")
                    else:
                        d = stock.history(interval="1m", start=target_date, end=end_date)
                        
                        # [Patch] Get Official Close (for synthetic 13:30 candle) AND Previous Close (for correct % change)
                        try:
                            # Fetch wider range (10 days) to find previous trading day
                            dt_target = datetime.strptime(target_date, "%Y-%m-%d")
                            start_lookback = (dt_target - timedelta(days=10)).strftime("%Y-%m-%d")
                            
                            d_daily = stock.history(interval="1d", start=start_lookback, end=end_date)
                            
                            if not d_daily.empty:
                                # Match target date
                                # d_daily index might be TZ-aware, target_date is naive string
                                d_daily_dates = d_daily.index.date 
                                target_date_obj = dt_target.date()
                                
                                if target_date_obj in d_daily_dates:
                                    # Locate
                                    locs = np.where(d_daily_dates == target_date_obj)[0]
                                    if len(locs) > 0:
                                        pos = locs[0]
                                        
                                        # 1. Get Official Close
                                        official_close = d_daily.iloc[pos]['Close']
                                        
                                        # 2. Get Previous Close
                                        if pos > 0:
                                            p_close = d_daily.iloc[pos-1]['Close']
                                            # print(f"DEBUG: Found Historical PrevClose: {p_close}")
                                        
                                        # 3. Apply Patch if Minute Data is mismatched
                                        if not d.empty:
                                            last_min_close = d['Close'].iloc[-1]
                                            if abs(official_close - last_min_close) > 0.01:
                                                # Create timestamp for 13:30
                                                tz = d.index.tz
                                                close_dt = datetime.combine(target_date_obj, datetime.strptime("13:30", "%H:%M").time())
                                                
                                                if tz:
                                                    close_dt = pd.Timestamp(close_dt).tz_localize(tz)
                                                else:
                                                    close_dt = pd.Timestamp(close_dt)
                                                    
                                                new_row = pd.DataFrame(index=[close_dt], data={
                                                    'Open': official_close, 'High': official_close, 
                                                    'Low': official_close, 'Close': official_close, 
                                                    'Volume': 0, 'Dividends': 0, 'Stock Splits': 0
                                                })
                                                d = pd.concat([d, new_row])
                                                print(f"DEBUG: Patched Close {last_min_close} -> {official_close}")
                        except Exception as e_patch:
                             print(f"Patch/PrevClose Error: {e_patch}")

                    return d, p_close, c_price
                except Exception as e:
                    print(f"YF Error {ticker}: {e}")
                    return None, None, None

            # Try TW then TWO
            new_df, prev_close, curr_price_info = fetch_yf(".TW")
            if new_df is None or new_df.empty:
                new_df, prev_close, curr_price_info = fetch_yf(".TWO")
            
            # Use New Data if valid
            if new_df is not None and not new_df.empty:
                 # Check Date Match
                 try:
                    data_date = new_df.index[0].date()
                    req_date = datetime.strptime(target_date, "%Y-%m-%d").date()
                    if data_date == req_date:
                        df = new_df # Update
                        loaded_from_cache = False # It's fresh
                    else:
                        # Mismatch
                        if cached_df is not None:
                             df = cached_df # Revert
                             print("DEBUG: Fetched date mismatch, reverting to cache.")
                        else:
                             df = None
                 except:
                    df = new_df
            elif cached_df is not None:
                # Fetch failed, but we have backup!
                df = cached_df
                print("DEBUG: Fetch failed/empty, reusing incomplete cache.")
            else:
                df = None

            if df is not None and not df.empty and prev_close is not None:
                # [Persist] Embed PrevClose in DataFrame so it survives Cache Save/Load
                df['PrevClose'] = prev_close

            # Save to Cache (DB) if valid OR if it's a past date (Negative Cache)
            if self.cache_manager:
                should_cache = False
                json_out = None
                
                # Only cache if data exists AND it is NOT today (historical final data)
                # AND it is better than our backup? (Well if we fetched it, it's likely better or same)
                if df is not None and not df.empty and target_date != today_str:
                    # If we reverted to cached_df during a refetch, do we save again? 
                    # If it was 'needs_refetch', we probably shouldn't overwrite unless we have BETTER data.
                    # BUT current logic: if df is set, we save. 
                    # If df == cached_df (because fetch failed), we are saving the SAME data. Harmless.
                    json_out = df.to_json(date_format='iso')
                    should_cache = True
                elif target_date != today_str and df is None:
                    # If fetch failed AND no cache backup -> Negative Cache
                    # BE CAREFUL: If fetch failed but we had cache (restored above), df is NOT None.
                    # So we only write [] if we truly have NOTHING.
                    json_out = "[]"
                    should_cache = True
                    # print(f"DEBUG: Saving Negative Cache for {self.stock_id} {target_date}")
                    
                if should_cache and json_out:
                    try:
                        # print(f"DEBUG: Saving Cache {self.stock_id}: {json_out[:20] if json_out else 'None'}")
                        self.cache_manager.save_chart_data(self.stock_id, target_date, json_out)
                    except Exception as e:
                        print(f"DB Cache Save Error: {e}")

        if df is None or df.empty:
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            
            # Custom Message checks
            msg = "No Data"
            try:
                dt = datetime.strptime(target_date, "%Y-%m-%d")
                now = datetime.now()
                if dt.date() == now.date() and now.hour < 9:
                    msg = "尚未開盤"
                elif dt.date() == now.date() and now.hour == 9 and now.minute < 1:
                     msg = "等待開盤..."
            except:
                pass
                
            ax.text(0.5, 0.5, msg, ha='center', va='center', fontsize=9, color='gray')
            ax.axis('off')
            self.canvas.draw()
            return

        # Prepare Plot
        try:
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            
            # Ref Price logic
            if prev_close:
                 ref_price = prev_close
            elif 'PrevClose' in df.columns and pd.notna(df['PrevClose'].iloc[0]):
                ref_price = df['PrevClose'].iloc[0]
            else:
                 ref_price = df['Close'].iloc[0] # Fallback
            
            # Current Price Logic
            if curr_price_info:
                current_price = curr_price_info
            else:
                current_price = df['Close'].iloc[-1]
            
            try:
                pct_change = ((current_price - ref_price) / ref_price) * 100
            except:
                pct_change = 0.0
                
            color = 'red' if pct_change >= 0 else 'green'
            
            # Plot Line
            ax.plot(df.index, df['Close'], label='Close', linewidth=1.2, color=color)
            
            # Ref Line
            ax.axhline(y=ref_price, color='gray', linestyle='--', linewidth=0.8, alpha=0.8)
            
            # Title with %
            title_dt = self.date_str[5:]
            sign = "+" if pct_change > 0 else ""
            title_text = f"{title_dt} {self.title_suffix}  {current_price:.2f} ({sign}{pct_change:.2f}%)"
            
            # Adjust Title Position or Font
            ax.set_title(title_text, fontsize=10, color=color, fontweight='bold', pad=4)
            ax.grid(True, linestyle='--', alpha=0.4)
            ax.tick_params(axis='both', which='major', labelsize=7)
            
            self.figure.autofmt_xdate(rotation=30)
            self.figure.tight_layout(pad=0.5) 
            self.canvas.draw()
        except Exception as e:
            print(f"Chart Plot Error: {e}")

class HistoryPage(QWidget):
    def __init__(self, history_manager):
        super().__init__()
        self.manager = history_manager
        self.cache_manager = CacheManager() # Reuse DB connection
        self.is_loaded = False
        self.init_ui()
        
    def showEvent(self, event):
        if not self.is_loaded:
            self.load_items()
            self.is_loaded = True
        super().showEvent(event)
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # Toolbar
        toolbar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜尋股號/股名...")
        self.search_input.textChanged.connect(self.filter_items)
        
        toolbar.addWidget(QLabel("搜尋:"))
        toolbar.addWidget(self.search_input)
        
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["日期 (新->舊)", "日期 (舊->新)", "股號"])
        self.sort_combo.currentIndexChanged.connect(self.sort_items)
        toolbar.addWidget(QLabel("排序:"))
        toolbar.addWidget(self.sort_combo)
        
        layout.addLayout(toolbar)
        
        # Table Widget
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "日期", "股號/名稱", "觸發說明", 
            "聽牌日(T)", "狀態", "隔日(T+1)", "隔二日(T+2)", "註解"
        ])
        
        # Adjust header sizing
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents) # Date
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents) # Code
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch) # Trigger (Text) - Swapped
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed) # Chart - Swapped
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents) # Status - Swapped
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed) # Chart
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed) # Chart
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive) # Comment
        
        # Fixed width for charts (compact)
        self.table.setColumnWidth(3, 280) # Chart T
        self.table.setColumnWidth(5, 280) # Chart T+1
        self.table.setColumnWidth(6, 280) # Chart T+2
        self.table.setColumnWidth(7, 200)
        
        self.table.verticalHeader().setDefaultSectionSize(180) # Default Row Height
        
        layout.addWidget(self.table)
        
        
    def load_items(self):
        self.table.setRowCount(0)
        records = self.manager.get_all()
        
        # Sort based on Combo (Default New->Old)
        mode = self.sort_combo.currentText()
        if "舊->新" in mode:
            records.sort(key=lambda x: x['date'])
        elif "股號" in mode:
            records.sort(key=lambda x: x['code'])
        else: # Default New->Old
            records.sort(key=lambda x: x['date'], reverse=True)
            
        self.table.setRowCount(len(records))
        
        # [Filter Logic] Remove consecutive duplicate "Listening" records
        # If Stock S was Listening on Day D, and is also Listening on Day D+1 (Next Trading Day),
        # Suppress the D+1 row. The D row will show the D+1 chart in its "T+1" column.
        
        # Sort by Date Ascending for processing
        records.sort(key=lambda x: x['date']) # Ascending
        
        filtered_records = []
        last_seen = {} # {code: datetime_obj of last record}
        
        for r in records:
             code = r['code']
             curr_date = datetime.strptime(r['date'], "%Y-%m-%d")
             
             is_duplicate = False
             if code in last_seen:
                 last_dt = last_seen[code]
                 # Calculate next trading day from last record
                 expected_next = DateUtils.get_next_trading_day(last_dt)
                 if curr_date.date() == expected_next.date():
                     is_duplicate = True
                     
             if not is_duplicate:
                 filtered_records.append(r)
                 
             # Update last seen (even if duplicate, so we can chain skip if needed?)
             # User says "Today (1/6) just update yesterday (1/5) record".
             # If 1/7 also listening? Should 1/5 show T+2? Yes.
             # If 1/8 also listening? 1/5 maxes at T+2. So 1/8 should probably appear?
             # But the table only has T, T+1, T+2.
             # Ideally, if chain > 3 days, we might need a new row or just stop tracking.
             # But for now, filtering duplicates is the main request.
             last_seen[code] = curr_date

        # Re-sort based on user selection
        if "舊->新" in mode:
             filtered_records.sort(key=lambda x: x['date'])
        elif "股號" in mode:
             filtered_records.sort(key=lambda x: x['code'])
        else: # Default New->Old
             filtered_records.sort(key=lambda x: x['date'], reverse=True)

        self.table.setRowCount(len(filtered_records))
        
        fetch_delay_counter = 0
        for r_idx, r in enumerate(filtered_records):
            self.table.setRowHeight(r_idx, 200) # Row Height for charts
            
            # 0. Date
            self.table.setItem(r_idx, 0, QTableWidgetItem(r['date']))
            
            # 1. Code/Name
            name = r.get('name', '')
            code = r['code']
            item_name = QTableWidgetItem(f"{code}\n{name}")
            item_name.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r_idx, 1, item_name)
            
            # 2. Trigger Info (Swapped to Col 2)
            trigger_info = r.get('trigger_info', '')
            if isinstance(trigger_info, dict):
                t_str = "\n".join([f"{k}: {v}" for k, v in trigger_info.items()])
                trigger_info = t_str
            item_trig = QTableWidgetItem(str(trigger_info))
            item_trig.setToolTip(str(trigger_info))
            self.table.setItem(r_idx, 2, item_trig)
            
            # Charts Variables
            token = "unused" 
            code = r['code']
            base_date_str = r['date']
            
            try:
                base_dt = datetime.strptime(base_date_str, "%Y-%m-%d")
            except:
                base_dt = datetime.now()
            
            # [Optimization] Smart Delay:
            # - Cached items: Fast ripple (30ms spacing)
            # - Uncached items: Staggered fetch (1500ms spacing) to avoid Rate Limit
            visual_delay = r_idx * 30 
            
            # 3. Chart T
            t_cached = False
            if self.cache_manager and self.cache_manager.get_chart_data(code, base_date_str):
                t_cached = True
            
            if t_cached:
                d1 = visual_delay
            else:
                d1 = max(visual_delay, fetch_delay_counter)
                fetch_delay_counter += 1500
                
            c1 = HistoryChartWidget(token, code, base_date_str, "(聽牌)", delay_ms=d1, cache_manager=self.cache_manager)
            self.table.setCellWidget(r_idx, 3, c1)
            
            # 4. Status (Swapped to Col 4)
            is_disposed = r.get("is_disposed_next_day", False)
            status_text = "進處置" if is_disposed else ""
            item_status = QTableWidgetItem(status_text)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_disposed:
                item_status.setForeground(Qt.GlobalColor.red)
                item_status.setFont(self.table.font()) # Default font
            self.table.setItem(r_idx, 4, item_status)

            # 5. Chart T+1
            dt2 = DateUtils.get_next_trading_day(base_dt)
            d_str2 = dt2.strftime("%Y-%m-%d")
            
            t2_cached = False
            if self.cache_manager and self.cache_manager.get_chart_data(code, d_str2):
                t2_cached = True
            
            if t2_cached:
                 d2 = visual_delay + 10 # Slight offset
            else:
                 d2 = max(visual_delay, fetch_delay_counter)
                 fetch_delay_counter += 1500
                 
            c2 = HistoryChartWidget(token, code, d_str2, "(隔日)", delay_ms=d2, cache_manager=self.cache_manager)
            self.table.setCellWidget(r_idx, 5, c2)
            
            # 6. Chart T+2
            dt3 = DateUtils.get_next_trading_day(dt2)
            d_str3 = dt3.strftime("%Y-%m-%d")
            
            t3_cached = False
            if self.cache_manager and self.cache_manager.get_chart_data(code, d_str3):
                t3_cached = True
            
            if t3_cached:
                 d3 = visual_delay + 20
            else:
                 d3 = max(visual_delay, fetch_delay_counter)
                 fetch_delay_counter += 1500
                 
            c3 = HistoryChartWidget(token, code, d_str3, "(隔二日)", delay_ms=d3, cache_manager=self.cache_manager)
            self.table.setCellWidget(r_idx, 6, c3)
            
            # 7. Comment Widget
            comm_widget = QWidget()
            comm_layout = QVBoxLayout(comm_widget)
            comm_layout.setContentsMargins(2,2,2,2)
            
            comm_edit = QTextEdit()
            comm_edit.setPlainText(r.get("comment", ""))
            comm_edit.setPlaceholderText("輸入註解...")
            
            comm_btn = QPushButton("存註解")
            comm_btn.setFixedHeight(25)
            
            # Use default argument binding to capture specific record
            def save_comment(checked=False, text_edit=comm_edit, record=r):
                 new_comm = text_edit.toPlainText()
                 self.manager.update_record(record['date'], record['code'], comment=new_comm)
                 comm_btn.setText("已儲存")
                 QApplication.processEvents() # Force update
                 # Reset text after delay? Nah.
                 
            comm_btn.clicked.connect(save_comment)
            
            comm_layout.addWidget(comm_edit)
            comm_layout.addWidget(comm_btn)
            self.table.setCellWidget(r_idx, 7, comm_widget)

    def filter_items(self):
        txt = self.search_input.text().lower()
        for r in range(self.table.rowCount()):
            # Check col 1 (Code/Name)
            item = self.table.item(r, 1)
            if item:
                match = txt in item.text().lower()
                self.table.setRowHidden(r, not match)
                
    def sort_items(self):
        self.load_items()
