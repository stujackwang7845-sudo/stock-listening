from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel, 
    QPushButton, QLineEdit, QComboBox, QFrame, QTextEdit, 
    QGridLayout, QSizePolicy, QToolBar, QMenu, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QApplication
)
from ui.flow_layout import FlowLayout
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QAction, QIcon, QColor, QCursor
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
import requests
import io

from core.history_manager import HistoryManager
from core.tag_manager import TagManager
from core.utils import DateUtils
from core.cache import CacheManager
import io
import os
from dotenv import load_dotenv

# Fugle 行情金鑰改由 .env 集中管理（2026-07-07，取代原本硬編）
# [Fix 2026-10-01] 筆電外出版沒有 E 槽那個共用設定資料夾，找不到就退回專案根目錄的 .env
_ENV_PATH = r"E:\Vibe Coding\ANTIGRAVITY SETTINGS\.env"
if os.path.exists(_ENV_PATH):
    load_dotenv(_ENV_PATH)
else:
    _local_env = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
    if os.path.exists(_local_env):
        load_dotenv(_local_env)

class TagCommentEdit(QTextEdit):
    add_tag_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("輸入註解（自動儲存）...")
        
        # Add button
        self.add_btn = QPushButton("+", self)
        self.add_btn.setFixedSize(20, 20)
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.setStyleSheet("""
            QPushButton {
                background-color: white;
                color: #333;
                border: 1px solid #999;
                border-radius: 3px;
                font-weight: bold;
                font-size: 14px;
                padding: 0px;
            }
            QPushButton:hover { 
                background-color: #f0f0f0;
                border-color: #4da6ff;
            }
        """)
        self.add_btn.clicked.connect(self.add_tag_clicked)
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Position top-right
        self.add_btn.move(self.width() - 25, 5)

class HistoryChartWidget(QWidget):
    def __init__(self, token, stock_id, date_str, title_suffix="", stock_name="", delay_ms=0, cache_manager=None, source="", strict_local=False):
        super().__init__()
        self.cache_manager = cache_manager
        self.source = source
        self.strict_local = strict_local
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.figure = None
        self.canvas = None
        
        self.token = token
        self.stock_id = stock_id
        self.stock_name = stock_name
        self.date_str = date_str
        self.title_suffix = title_suffix
        self.is_no_cache = False
        
        # Show loading text using QLabel instead of Matplotlib
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: gray; font-size: 12px;")
        if self.strict_local:
            self.status_label.setText("Loading DB...")
        else:
            self.status_label.setText("Loading...")
        self.layout.addWidget(self.status_label)
        
        # [Optimization] Remove QTimer delay if delay_ms is 0 (Cached item) to render instantly.
        if delay_ms <= 0:
            self._fetch_data()
        else:
            QTimer.singleShot(delay_ms, self._fetch_data)


    def _init_canvas(self):
        if self.figure is None:
            self.figure = Figure(figsize=(4, 2.5), dpi=80)
            self.canvas = FigureCanvasQTAgg(self.figure)
            self.layout.addWidget(self.canvas)
            self.status_label.hide()
        else:
            self.status_label.hide()
            self.canvas.show()
            self.figure.clear()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        reload_action = menu.addAction("重新下載資料(Reload)")
        
        action = menu.exec(event.globalPos())
        if action == reload_action:
            if self.canvas:
                self.canvas.hide()
            self.status_label.setText("Reloading...")
            self.status_label.show()
            self._fetch_data(force_refresh=True)

    def _fetch_data(self, force_refresh=False):
        target_date = self.date_str
        end_date = None
        
        # Calculate End Date (Next Day) for yfinance range (start, end)
        try:
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            today = datetime.now()
            today_str = today.strftime("%Y-%m-%d")
            
            # Allow caching today if after 13:35
            limit_cache = datetime(today.year, today.month, today.day, 13, 35)
            can_cache_today = (today >= limit_cache)
            
            # Future Check (Strictly Future)
            if dt.date() > today.date():
                self._init_canvas()
                ax = self.figure.add_subplot(111)
                ax.text(0.5, 0.5, f"{target_date}\n尚未開盤", ha='center', va='center', fontsize=9, color='gray')
                ax.axis('off')
                self.canvas.draw()
                return

            # Today Check (Allow after 13:35, otherwise show 'Not Settled')
            if dt.date() == today.date():
                 limit_time = datetime(today.year, today.month, today.day, 13, 35)
                 if today < limit_time:
                    self._init_canvas()
                    ax = self.figure.add_subplot(111)
                    ax.text(0.5, 0.5, f"{target_date}\n資料尚未結算", ha='center', va='center', fontsize=9, color='gray')
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
        # Allow if NOT today OR (Today and After 13:35)
        allow_cache = (target_date != today_str) or (target_date == today_str and can_cache_today)
        
        if not force_refresh and allow_cache and self.cache_manager:
            try:
                json_str = self.cache_manager.get_chart_data(self.stock_id, target_date)
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
                            
                            # [Fix] Ignore "Incomplete" check if date is older than 7 days
                            # For delisted or historical stocks (like 4304), data might be permanently incomplete.
                            try:
                                dt_target = datetime.strptime(target_date, "%Y-%m-%d")
                                delta_days = (datetime.now() - dt_target).days
                                if delta_days > 5:
                                    needs_refetch = False
                                    print(f"DEBUG: incomplete 4304 check skipped (Data > 5 days old). Ignoring incomplete cache.")
                            except:
                                pass
                            
                            if needs_refetch:
                                print(f"DEBUG: Incomplete Cache {self.stock_id}: Last={last_time} < {market_close}. Triggering Refetch...")

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
            if self.strict_local and not force_refresh:
                # [Optimization] Local-Only Mode for History Page
                # If cannot find complete data in DB, just show placeholder. Don't fetch YF.
                self._init_canvas()
                ax = self.figure.add_subplot(111)
                
                # 若快取內明確記錄為空 {} (腳本標記為查無資料)，顯示「無資料」
                if df is not None and df.empty:
                     ax.text(0.5, 0.5, f"{target_date}\n(無分時資料)", ha='center', va='center', fontsize=9, color='gray')
                     self.is_no_cache = False
                else:
                     ax.text(0.5, 0.5, f"{target_date}\n無本地快取", ha='center', va='center', fontsize=9, color='gray')
                     self.is_no_cache = True
                     
                ax.axis('off')
                self.canvas.draw()
                return

            self.is_no_cache = False
            print(f"DEBUG: Fetching data for {self.stock_id} (Refetch={needs_refetch})")

            # [2026-07-07] 移除 yfinance（台股資料禁用 yfinance 規則）。
            # 主力來源改為 Fugle 1 分 K（下方原「Primary Fallback」區塊），
            # FinMind 分時為次要備援。前收由 FinMind 日 K 提供。
            new_df, prev_close, curr_price_info = None, None, None

            # [Fix] Fugle API 主力來源（1 分 K）
            if new_df is None or new_df.empty:
                print(f"DEBUG: Fetching Fugle 1m for {self.stock_id} at {target_date}...")
                try:
                    import requests
                    api_key = os.getenv("FUGLE_API_KEY", "")
                    url = f"https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/{self.stock_id}?from={target_date}&to={target_date}&timeframe=1"
                    response = requests.get(url, headers={"X-API-KEY": api_key}, timeout=5)
                    if response.status_code == 200:
                        data = response.json().get("data", [])
                        if data:
                            f_df = pd.DataFrame(data)
                            f_df['date'] = pd.to_datetime(f_df['date'])
                            f_df.set_index('date', inplace=True)
                            f_df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
                            f_df = f_df.sort_index()
                            # Fugle 可能會回傳多天資料，篩選出我們要的那一天
                            f_df_target = f_df[f_df.index.date == datetime.strptime(target_date, "%Y-%m-%d").date()]
                            if not f_df_target.empty and len(f_df_target) > 1:
                                new_df = f_df_target.copy()
                                
                                # 使用 FinMind 獲取前一日收盤價以計算漲跌幅
                                try:
                                    from core.finmind_client import FinMindClient
                                    fm = FinMindClient()
                                    dt_target = datetime.strptime(target_date, "%Y-%m-%d")
                                    start_lookback = (dt_target - timedelta(days=10)).strftime('%Y-%m-%d')
                                    fm_daily = fm.fetch_daily_price(self.stock_id, start_date=start_lookback, end_date=target_date)
                                    
                                    if fm_daily is not None and not fm_daily.empty:
                                        fm_daily['date_obj'] = pd.to_datetime(fm_daily['date'])
                                        past_days = fm_daily[fm_daily['date_obj'] < pd.to_datetime(target_date)]
                                        
                                        if not past_days.empty:
                                            past_days = past_days.sort_values('date_obj')
                                            prev_close = float(past_days.iloc[-1]['close'])
                                except Exception as ep:
                                    print(f"Fugle->FinMind Daily PrevClose error: {ep}")
                                
                                curr_price_info = float(new_df['Close'].iloc[-1])
                                print(f"DEBUG: Successfully fetched Fugle data for {self.stock_id}. Points: {len(new_df)}")
                except Exception as e:
                    print(f"Fugle fallback error: {e}")

            # [Fix] FinMind API Fallback (Secondary Fallback)
            # 當 yfinance 和 Fugle 皆失敗時，改用 FinMind 下載歷史分時資料
            if new_df is None or new_df.empty:
                print(f"DEBUG: Fugle Fetch failed for {self.stock_id} at {target_date}, falling back to FinMind...")
                from core.finmind_client import FinMindClient
                try:
                    fm = FinMindClient()
                    fm_df = fm.fetch_minute_price(self.stock_id, target_date)
                    if fm_df is not None and not fm_df.empty:
                        # 轉換為 yfinance 格式
                        fm_df['datetime'] = pd.to_datetime(fm_df['date'] + ' ' + fm_df['time'])
                        fm_df.set_index('datetime', inplace=True)
                        fm_df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
                        
                        # 若只有單一筆或異常資料則視同無資料
                        if len(fm_df) > 1:
                            new_df = fm_df
                            
                            # 取得昨日收盤價計算漲跌幅
                            try:
                                dt_target = datetime.strptime(target_date, "%Y-%m-%d")
                                start_lookback = (dt_target - timedelta(days=10)).strftime('%Y-%m-%d')
                                fm_daily = fm.fetch_daily_price(self.stock_id, start_date=start_lookback, end_date=target_date)
                                
                                if fm_daily is not None and not fm_daily.empty:
                                    fm_daily['date_obj'] = pd.to_datetime(fm_daily['date'])
                                    past_days = fm_daily[fm_daily['date_obj'] < pd.to_datetime(target_date)]
                                    
                                    if not past_days.empty:
                                        past_days = past_days.sort_values('date_obj')
                                        prev_close = float(past_days.iloc[-1]['close'])
                            except Exception as ep:
                                print(f"FinMind Daily PrevClose error: {ep}")
                                
                            curr_price_info = float(new_df['Close'].iloc[-1])
                            print(f"DEBUG: Successfully fetched FinMind data for {self.stock_id}. Points: {len(new_df)}")
                except Exception as e:
                    print(f"FinMind fallback error: {e}")
            
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
                
                # [Fix] Critical Patch for Infinite Loop
                # If we needed a refetch (incomplete data) but the fetch failed (e.g. 404/Delisted),
                # we MUST patch the local cache so it looks "complete" (>= 13:00).
                # Otherwise, it will retry fetching forever.
                if needs_refetch and not df.empty:
                    try:
                        last_dt = df.index[-1]
                        # Create target 13:30 timestamp on the same date
                        # Ensure timezone awareness matches
                        manual_close_time = last_dt.replace(hour=13, minute=30, second=0)
                        
                        # Only patch if the last record is actually earlier than 13:30
                        # (and it's the same day, don't patch random dates)
                        if last_dt < manual_close_time:
                            print(f"DEBUG: Patching incomplete cache for {self.stock_id} to stop refetch loop. (Adding 13:30 candle)")
                            
                            new_row = df.iloc[[-1]].copy()
                            new_row.index = [manual_close_time]
                            df = pd.concat([df, new_row])
                            
                            # Mark that we modified it, so the save block below triggers
                            # (loaded_from_cache might be False, but we want to force save this PATCHED version)
                            # Logic below checks `if df is not None and allow_save`. which is True.
                            
                    except Exception as e_patch:
                        print(f"Cache Patch Error for {self.stock_id}: {e_patch}")
                        
                else:
                    print(f"DEBUG: Fetch failed/empty for {self.stock_id}, reusing existing cache (Rows: {len(df) if df is not None else 0}).")
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
                # OR if it is today AND settled (after 13:35)
                # AND it is better than our backup? (Well if we fetched it, it's likely better or same)
                
                allow_save = (target_date != today_str) or (target_date == today_str and can_cache_today)
                
                if df is not None and not df.empty and allow_save:
                    # If we reverted to cached_df during a refetch, do we save again? 
                    # If it was 'needs_refetch', we probably shouldn't overwrite unless we have BETTER data.
                    # BUT current logic: if df is set, we save. 
                    # If df == cached_df (because fetch failed), we are saving the SAME data. Harmless.
                    json_out = df.to_json(date_format='iso')
                    should_cache = True
                elif allow_save and df is None:
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
            self._init_canvas()
            ax = self.figure.add_subplot(111)
            
            # Custom Message checks
            msg = "無分時資料"
            try:
                dt = datetime.strptime(target_date, "%Y-%m-%d")
                now = datetime.now()
                if dt.date() == now.date() and now.hour < 9:
                    msg = "尚未開盤"
                elif dt.date() == now.date() and now.hour == 9 and now.minute < 1:
                     msg = "等待開盤..."
                elif not force_refresh and self.cache_manager and allow_cache and df is not None and df.empty:
                     msg = "(無分時資料)"
            except:
                pass
                
            ax.text(0.5, 0.5, f"{target_date}\n{msg}", ha='center', va='center', fontsize=9, color='gray')
            ax.axis('off')
            self.canvas.draw()
            
            # [Persist Empty] Ensure we save empty dict to avoid showing "無本地快取" forever
            if df is None and not loaded_from_cache and self.cache_manager and allow_cache:
                try:
                    self.cache_manager.save_chart_data(self.stock_id, target_date, "{}")
                except:
                    pass
            return

        # Prepare Plot
        try:
            self._init_canvas()
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
            # Convert index to Taiwan Time for display
            df_plot = df.copy()
            
            # Debug: Print first timestamp
            # if not df_plot.empty:
            #     print(f"DEBUG Chart {self.stock_id} Raw Index[0]: {df_plot.index[0]} (TZ={df_plot.index.tz})")
            
            if df_plot.index.tz is None:
                # Assume UTC if naive and hour < 5
                if not df_plot.empty and df_plot.index[0].hour < 5: 
                     df_plot.index = df_plot.index + pd.Timedelta(hours=8)
            else:
                 df_plot.index = df_plot.index.tz_convert('Asia/Taipei')
            
            # [Crucial Step] Strip Timezone to force Matplotlib to use Wall Clock Time
            # This prevents Matplotlib from converting back to UTC or Local System Time
            df_plot.index = df_plot.index.tz_localize(None)

            # if not df_plot.empty:
            #     print(f"DEBUG Chart {self.stock_id} Plot Index[0]: {df_plot.index[0]}")

            ax.plot(df_plot.index, df_plot['Close'], label='Close', linewidth=1.2, color=color)
            
            # Ref Line
            ax.axhline(y=ref_price, color='gray', linestyle='--', linewidth=0.8, alpha=0.8)
            
            # Title with stock name and %
            title_dt = self.date_str[5:]
            sign = "+" if pct_change > 0 else ""
            stock_label = f"{self.stock_id} {self.stock_name}" if self.stock_name else self.stock_id
            title_text = f"{stock_label} {title_dt} {self.title_suffix}\n{current_price:.2f} ({sign}{pct_change:.2f}%)"
            
            # Adjust Title Position or Font
            ax.set_title(title_text, fontsize=10, color=color, fontweight='bold', pad=4)
            ax.grid(True, linestyle='--', alpha=0.4)
            ax.tick_params(axis='both', which='major', labelsize=7)
            
            # [Fix] X-Axis Formatting (Taiwan Time 09:00-13:30)
            import matplotlib.dates as mdates
            myFmt = mdates.DateFormatter('%H:%M') # TZ is None (Naive)
            ax.xaxis.set_major_formatter(myFmt)
            
            # Set Limits 09:00 - 13:30
            if not df_plot.empty:
                base_date = df_plot.index[0].date()
                start_limit = datetime.combine(base_date, datetime.strptime("09:00", "%H:%M").time())
                end_limit = datetime.combine(base_date, datetime.strptime("13:30", "%H:%M").time())
                
                # Naive Limits for Naive Index
                ax.set_xlim(start_limit, end_limit)

            # Rotate labels
            plt.setp(ax.get_xticklabels(), rotation=30, ha='right')
            self.figure.tight_layout(pad=0.5) 
            self.canvas.draw()
        except Exception as e:
            print(f"Chart Plot Error: {e}")

class HistoryPage(QWidget):
    def __init__(self, history_manager, tag_manager=None):
        super().__init__()
        self.manager = history_manager
        self.tag_manager = tag_manager if tag_manager else TagManager()
        self.cache_manager = CacheManager() # Reuse DB connection
        # [NEW] 可轉債（CB）資料庫
        from core.cb_data import CBDatabase
        self.cb_db = CBDatabase()
        self.is_loaded = False
        # 分頁狀態
        self.page_size = 50
        self.current_page = 0        # 0-indexed
        self.filtered_records = []   # 過濾後全部記錄，由 _build_filtered_records() 結果
        self.init_ui()
        
    def showEvent(self, event):
        if not self.is_loaded:
            self.load_items()
            self.is_loaded = True
        super().showEvent(event)
        
    def init_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        
        self.main_scroll = QScrollArea()
        self.main_scroll.setWidgetResizable(True)
        self.main_scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        self.main_widget_content = QWidget()
        layout = QVBoxLayout(self.main_widget_content)
        
        self.main_scroll.setWidget(self.main_widget_content)
        outer_layout.addWidget(self.main_scroll)
        
        # Toolbar
        toolbar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜尋股號/股名/檢討/註解...")
        self.search_input.textChanged.connect(self.filter_items)
        
        toolbar.addWidget(QLabel("搜尋:"))
        toolbar.addWidget(self.search_input)
        
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["日期 (新->舊)", "日期 (舊->新)", "股號"])
        self.sort_combo.currentIndexChanged.connect(self.sort_items)
        toolbar.addWidget(QLabel("排序:"))
        toolbar.addWidget(self.sort_combo)
        
        # Tag 過濾器
        self.tag_filter_combo = QComboBox()
        self.tag_filter_combo.addItem("所有標籤", None)
        self.tag_filter_combo.currentIndexChanged.connect(self.filter_items)
        toolbar.addWidget(QLabel("標籤:"))
        toolbar.addWidget(self.tag_filter_combo)
        
        # [NEW] Refresh Button
        self.refresh_btn = QPushButton(" 刷新數據")
        from PyQt6.QtWidgets import QStyle
        self.refresh_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.refresh_btn.clicked.connect(self.load_items)
        toolbar.addSpacing(10)
        toolbar.addWidget(self.refresh_btn)
        
        layout.addLayout(toolbar)
        
        # Table Widget
        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "日期", "股號/名稱", "觸發說明", 
            "聽牌日(T)", "隔日(T+1)", "狀態", "頻率", "隔二日(T+2)", "註解", "檢討"
        ])
        
        # Adjust header sizing
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents) # Date
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents) # Code
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed) # Trigger (Fixed Spec)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed) # Chart - Swapped
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed) # Chart T+1 - Swapped
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents) # Status
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents) # Frequency
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed) # Chart T+2
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Interactive) # Comment
        header.setSectionResizeMode(9, QHeaderView.ResizeMode.Interactive) # Review
        
        # Fixed width
        self.table.setColumnWidth(2, 420)
        self.table.setColumnWidth(3, 280) # Chart T
        self.table.setColumnWidth(4, 280) # Chart T+1
        self.table.setColumnWidth(7, 280) # Chart T+2
        self.table.setColumnWidth(8, 350) # Comment
        self.table.setColumnWidth(9, 350) # Review
        
        self.table.verticalHeader().setDefaultSectionSize(180) # Default Row Height
        
        layout.addWidget(self.table)
        
        # 分頁工具列
        pager_layout = QHBoxLayout()
        pager_layout.addStretch()
        
        self.batch_update_btn = QPushButton("批量下載無快取圖表")
        self.batch_update_btn.clicked.connect(self._batch_update_charts)
        self.batch_update_btn.setStyleSheet("""
            QPushButton {
                background-color: #2b2b2b;
                color: #e0e0e0;
                border: 1px solid #5a5a5a;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: #3b3b3b;
                border: 1px solid #7a7a7a;
            }
            QPushButton:disabled {
                background-color: #1a1a1a;
                color: #555555;
            }
        """)
        pager_layout.addWidget(self.batch_update_btn)
        pager_layout.addSpacing(10)
        
        self.prev_btn = QPushButton("‹ 上一頁")
        self.prev_btn.setFixedWidth(80)
        self.prev_btn.clicked.connect(self._prev_page)
        pager_layout.addWidget(self.prev_btn)
        
        pager_layout.addSpacing(10)
        
        self.page_input = QLineEdit("1")
        self.page_input.setFixedWidth(40)
        self.page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_input.returnPressed.connect(self._jump_page)
        pager_layout.addWidget(self.page_input)
        
        pager_layout.addSpacing(5)
        
        self.page_total_label = QLabel("/ 1")
        self.page_total_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        pager_layout.addWidget(self.page_total_label)
        
        pager_layout.addSpacing(10)
        
        self.next_btn = QPushButton("下一頁 ›")
        self.next_btn.setFixedWidth(80)
        self.next_btn.clicked.connect(self._next_page)
        pager_layout.addWidget(self.next_btn)
        
        pager_layout.addStretch()
        layout.addLayout(pager_layout)
        


    def _build_filtered_records(self):
        """(分頁輔助) 根據搜尋文字、Tag、排序建立過濾清單，存入 self.filtered_records"""
        txt = self.search_input.text().lower()
        selected_tag = self.tag_filter_combo.currentData()
        mode = self.sort_combo.currentText()
        
        records = self.manager.get_all()
        
        # Sort
        if '舊->新' in mode:
            records.sort(key=lambda x: x['date'])
        elif '股號' in mode:
            records.sort(key=lambda x: x['code'])
        else:
            records.sort(key=lambda x: x['date'], reverse=True)
            
        # Filter by text, tag, and trading day
        result = []
        for r in records:
            # 1. 確保該日期為交易日，排除假日誤抓的記錄
            date_str = r.get('date', '')
            is_valid_day = True
            if date_str:
                try:
                    dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
                    if not DateUtils.is_trading_day(dt_obj):
                        is_valid_day = False
                except:
                    pass
            if not is_valid_day:
                continue
                
            code = str(r.get('code', ''))
            name = str(r.get('name', ''))
            
            # 依使用者要求：直接顯示出現在歷史紀錄（聽牌區原始資料）的股票，不過濾條款
            # 只過濾掉權證/牛熊證（5~6碼）以及小數點異常代號，對齊 Dashboard 的邏輯
            if len(code) > 4 or "." in code:
                continue
                
                
            comment = str(r.get('comment', '') or '').lower()
            review  = str(r.get('review',  '') or '').lower()
            text_match = (
                (not txt)
                or (txt in code.lower())
                or (txt in name.lower())
                or (txt in comment)
                or (txt in review)
            )
            
            tag_match = True
            if selected_tag:
                tag_match = selected_tag in r.get('tags', [])
            
            if text_match and tag_match:
                result.append(r)
                
        self.filtered_records = result

    def _total_pages(self):
        return max(1, (len(self.filtered_records) + self.page_size - 1) // self.page_size)
        
    def _update_pager_ui(self):
        total = self._total_pages()
        self.page_input.setText(str(self.current_page + 1))
        self.page_total_label.setText(f"/ {total}")
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(self.current_page < total - 1)
        
    def _jump_page(self):
        try:
            target_page = int(self.page_input.text())
        except ValueError:
            target_page = self.current_page + 1
            
        total = self._total_pages()
        if target_page < 1:
            target_page = 1
        elif target_page > total:
            target_page = total
            
        if target_page - 1 == self.current_page:
            self.page_input.setText(str(target_page))
            return
            
        self.current_page = target_page - 1
        self.load_items()

    def _prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.load_items()
            
    def _next_page(self):
        if self.current_page < self._total_pages() - 1:
            self.current_page += 1
            self.load_items()

    def _batch_update_charts(self):
        self._update_queue = []
        for row in range(self.table.rowCount()):
            for col in [3, 4, 7]:
                widget = self.table.cellWidget(row, col)
                if isinstance(widget, HistoryChartWidget) and getattr(widget, 'is_no_cache', False):
                    self._update_queue.append(widget)
        
        if not self._update_queue:
            QMessageBox.information(self, "批量下載", "目前頁面沒有需要更新的圖表。")
            return
            
        reply = QMessageBox.question(self, "批量下載", f"共找到 {len(self._update_queue)} 個無快取圖表。\\n是否要開始逐一下載更新？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.batch_update_btn.setEnabled(False)
            self._update_total = len(self._update_queue)
            self.batch_update_btn.setText(f"更新中 (0/{self._update_total})...")
            self._process_next_chart()
            
    def _process_next_chart(self):
        if not hasattr(self, '_update_queue') or not self._update_queue:
            self.batch_update_btn.setEnabled(True)
            self.batch_update_btn.setText("批量下載無快取圖表")
            QMessageBox.information(self, "完成", "批量下載已完成！")
            return
            
        chart = self._update_queue.pop(0)
        try:
            if chart.canvas:
                chart.canvas.hide()
            chart.status_label.setText("Downloading...")
            chart.status_label.show()
        except Exception:
            pass
            
        try:
            chart._fetch_data(force_refresh=True)
        except Exception as e:
            print(f"Batch Update Error for {chart.stock_id}: {e}")
            
        completed = self._update_total - len(self._update_queue)
        self.batch_update_btn.setText(f"更新中 ({completed}/{self._update_total})...")
        
        # 增加延遲到 800ms，避免過度頻繁請求被 Yahoo/Fugle 封鎖
        QTimer.singleShot(800, self._process_next_chart)

    def load_items(self):
        """載入歷史紀錄並建立過濾列表"""
        print(f"[HistoryPage] 正在載入歷史紀錄... (總筆數: {len(self.manager.history)})")
        
        # 更新 Tag 過濾器
        self._update_tag_filter()
        
        # 建立過濾清單（整合搜尋文字、Tag、排序）
        self._build_filtered_records()
        
        # [Fix] 除非是翻頁呼叫，否則重置到第一頁
        # 這裡我們不強制 current_page = 0，讓 pager 元件自己控制
        
        # 取出當頁資料（每頁 page_size 筆）
        start = self.current_page * self.page_size
        end = start + self.page_size
        page_records = self.filtered_records[start:end]
        
        print(f"[HistoryPage] 過濾後共 {len(self.filtered_records)} 筆，目前顯示第 {self.current_page + 1} 頁 (索引 {start}-{end})")
        
        self.table.setRowCount(len(page_records))
        
        # 設定行號：接續上一頁（例如 51-100）
        labels = [str(start + i + 1) for i in range(len(page_records))]
        self.table.setVerticalHeaderLabels(labels)
        
        fetch_delay_counter = 0
        for r_idx, r in enumerate(page_records):
            self.table.setRowHeight(r_idx, 200) # Row Height for charts
            
            # 0. Date
            self.table.setItem(r_idx, 0, QTableWidgetItem(r['date']))
            
            # 1. Code/Name with Futures Badge
            name = r.get('name', '')
            code = r['code']
            has_futures = r.get('has_futures', False)
            source = r.get('source', '')
            
            # Build display text: Code \n Name \n (期)(CB) if applicable
            display_text = f"{code}\n{name}"
            suffix_line = ""
            if has_futures:
                suffix_line += "(期)"
            if hasattr(self, 'cb_db') and self.cb_db.had_cb_on(code, r['date']):
                suffix_line += "(CB)"
            if suffix_line:
                display_text += f"\n{suffix_line}"
            
            item_name = QTableWidgetItem(display_text)
            item_name.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r_idx, 1, item_name)
            
            # 2. Trigger Info (Swapped to Col 2)
            # 2. Trigger Info (HTML Table Spec)
            trigger_info = r.get('trigger_info', '')
            t_widget = self._render_trigger_cell(trigger_info, r['date'], code)
            self.table.setCellWidget(r_idx, 2, t_widget)
            
            # Charts Variables
            token = "unused" 
            code = r['code']
            base_date_str = r['date']
            
            try:
                base_dt = datetime.strptime(base_date_str, "%Y-%m-%d")
            except:
                base_dt = datetime.now()
            
            # 加大延遲，讓UI有機會刷新（分批非同步渲染避免卡死主畫面）
            # 每列 (row) 之間間隔 100ms，每張圖表間隔 30ms
            base_delay = 50 + (r_idx * 100)
            
            # 3. Chart T
            d1 = base_delay
                
            c1 = HistoryChartWidget(token, code, base_date_str, "(聽牌)", stock_name=name, delay_ms=d1, cache_manager=self.cache_manager, source=source, strict_local=True)
            self.table.setCellWidget(r_idx, 3, c1)
            
            # 4. Chart T+1 (Swapped to Col 4)
            dt2 = DateUtils.get_next_trading_day(base_dt)
            d_str2 = dt2.strftime("%Y-%m-%d")
            
            d2 = base_delay + 30
                 
            c2 = HistoryChartWidget(token, code, d_str2, "(隔日)", stock_name=name, delay_ms=d2, cache_manager=self.cache_manager, source=source, strict_local=True)
            self.table.setCellWidget(r_idx, 4, c2)

            # 5. Status (Swapped to Col 5)
            # [Fix] History Status Accuracy
            # Use Actual Disposal Database and Cache to check if it really entered disposal on T+1
            status_text = ""
            is_disposed_pred = r.get("is_disposed_next_day", False)
            
            # Calculate T+1 Date
            try:
                dt_base = datetime.strptime(r['date'], "%Y-%m-%d")
                dt_next = DateUtils.get_next_trading_day(dt_base)
                next_date_str = dt_next.strftime("%Y-%m-%d")
                
                # Check factual database unconditionally
                if not hasattr(self, 'disposal_db'):
                    from core.disposal_database import DisposalDatabase
                    self.disposal_db = DisposalDatabase()
                    
                is_actual_disposed = self.disposal_db.is_announced_on(code, next_date_str)
                
                # If not in DB, check next-day cache (e.g., today's new announcement for tomorrow)
                if not is_actual_disposed:
                    agg_date_str = next_date_str.replace("-", "")
                    agg_data = self.cache_manager.get_agg_data(agg_date_str)
                    if agg_data and str(code) in agg_data:
                        ad_rec = agg_data[str(code)]
                        if ad_rec.get('future_measure') or ad_rec.get('future_period'):
                            is_actual_disposed = True
                
                # 檢查 T 日是否已經在處置中
                is_currently_disposed = False
                active_t = self.disposal_db.get_active_disposals(r['date'])
                if any(str(rec.get('code')) == str(code) for rec in active_t):
                    is_currently_disposed = True
                else:
                    # Fallback to cache for T day
                    agg_date_str_t = r['date'].replace("-", "")
                    agg_data_t = self.cache_manager.get_agg_data(agg_date_str_t)
                    if agg_data_t and str(code) in agg_data_t:
                        if agg_data_t[str(code)].get('is_disposed', False):
                            is_currently_disposed = True

                if is_actual_disposed:
                    status_text = "進處置"
                else:
                    # [User Request] 移除預測欄位的「處置中」，改為統一顯示在觸發說明下方
                    status_text = "" # Safe, did not enter

            except Exception as e:
                print(f"Status Check Error: {e}")

            item_status = QTableWidgetItem(status_text)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if "進處置" in status_text:
                item_status.setForeground(Qt.GlobalColor.red)
                item_status.setFont(self.table.font()) # Default font
            elif "處置中" in status_text:
                item_status.setForeground(QColor(255, 165, 0)) # Orange
                item_status.setFont(self.table.font())
            self.table.setItem(r_idx, 5, item_status)
            
            # 6. Frequency (New)
            freq_text = ""
            if "進處置" in status_text or "處置中" in status_text:
                if hasattr(self, 'disposal_db'):
                    check_date = next_date_str if status_text == "進處置" else r['date']
                    
                    # Fetch disposal rule from database
                    act_recs = self.disposal_db.get_active_disposals(check_date)
                    act_recs = [rec for rec in act_recs if str(rec.get('code')) == str(code)]
                    
                    if status_text == "進處置":
                        dt_plus_1 = DateUtils.get_next_trading_day(dt_next)
                        next_plus_1_str = dt_plus_1.strftime("%Y-%m-%d")
                        future_recs = self.disposal_db.get_active_disposals(next_plus_1_str)
                        future_recs = [rec for rec in future_recs if str(rec.get('code')) == str(code)]
                        
                        all_recs = act_recs + future_recs
                        match_announce_str = next_date_str.replace('-', '/')
                        new_announcements = [rec for rec in all_recs if rec.get('announce_date') == match_announce_str]
                    else:
                        all_recs = act_recs
                        new_announcements = []
                    
                    def safe_date(d): return d if d else '0000-00-00'
                    
                    if new_announcements:
                        target_rec = new_announcements[0] # The new measure rules
                    elif all_recs:
                        all_recs.sort(key=lambda x: (safe_date(x.get('announce_date')), safe_date(x.get('period_start'))), reverse=True)
                        target_rec = all_recs[0]
                    else:
                        target_rec = None

                    if target_rec:
                        raw_freq = target_rec.get('measure') or ""
                    else:
                        # Fallback for predictions (e.g. today's newly announced disposal)
                        agg_date_str = check_date.replace("-", "")
                        agg_data = self.cache_manager.get_agg_data(agg_date_str)
                        raw_freq = ""
                        if agg_data and str(code) in agg_data:
                            ad_rec = agg_data[str(code)]
                            if status_text == "進處置":
                                raw_freq = ad_rec.get('future_measure') or ""
                            else:
                                raw_freq = ad_rec.get('measure') or ""
                                
                    if raw_freq:
                        from core.measure_parser import MeasureParser
                        freq_text = MeasureParser.parse_frequency(raw_freq)
                        if not freq_text:
                            freq_text = raw_freq
            
            item_freq = QTableWidgetItem(freq_text)
            item_freq.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r_idx, 6, item_freq)
            
            # 7. Chart T+2
            dt3 = DateUtils.get_next_trading_day(dt2)
            d_str3 = dt3.strftime("%Y-%m-%d")
            
            d3 = base_delay + 60
                 
            c3 = HistoryChartWidget(token, code, d_str3, "(隔二日)", stock_name=name, delay_ms=d3, cache_manager=self.cache_manager, source=source, strict_local=True)
            self.table.setCellWidget(r_idx, 7, c3)
            
            # 8. Comment Widget with Tag Support (Compact Design)
            comment_container = QWidget()
            comment_container.setMaximumHeight(180)  # 防止佈局超出版面
            comment_layout = QVBoxLayout(comment_container)
            comment_layout.setContentsMargins(4, 4, 4, 4)
            comment_layout.setSpacing(2)
            
            # 使用自定義的 TagCommentEdit
            comm_edit = TagCommentEdit()
            comm_val = r.get("comment", "")
            # 過濾掉 GitHub 自動擷取產生的預設無效字串
            if comm_val == "GitHub Actions 自動每日擷取":
                comm_val = ""
            comm_edit.setPlainText(comm_val)
            
            # 連接 Tag 按鈕訊號
            # Using default arg to bind container properly
            comm_edit.add_tag_clicked.connect(
                lambda rec=r, container=comment_container: self._show_tag_menu(rec, container)
            )
            
            comment_layout.addWidget(comm_edit, 1)  # 擴展
            
            # Tag 顯示區域 (只在有 Tag 時顯示，緊湊設計)
            tags = r.get("tags", [])
            if tags:
                tag_container = QWidget()
                tag_container.setObjectName('tag_container')
                tag_layout = FlowLayout(tag_container, margin=2, hSpacing=4, vSpacing=4)
                
                # 顯示已新增的 Tags
                for tag_id in tags:
                    badge = self._create_tag_badge(tag_id, r, removable=True)
                    if badge:
                        tag_layout.addWidget(badge)
                
                comment_layout.addWidget(tag_container)
            
            # Auto-save with delay to avoid excessive DB writes
            from PyQt6.QtCore import QTimer
            save_timer = QTimer(comm_edit)  # Set parent to prevent garbage collection
            save_timer.setSingleShot(True)
            save_timer.setInterval(500)  # 500ms delay after last keystroke
            
            # IMPORTANT: Use default arguments to bind current loop values (closure fix)
            def save_comment(text_edit=comm_edit, record=r):
                new_comm = text_edit.toPlainText()
                print(f"DEBUG: Auto-saving comment for {record['code']} on {record['date']}: '{new_comm[:30]}...'")
                self.manager.update_record(record['date'], record['code'], comment=new_comm)
                print(f"DEBUG: Comment saved successfully")
                
            save_timer.timeout.connect(save_comment)
            
            # IMPORTANT: Bind r['code'] via default argument to fix closure issue
            def on_text_changed(current_code=r['code'], timer=save_timer):
                print(f"DEBUG: Text changed for {current_code}, starting timer...")
                timer.start()
                
            comm_edit.textChanged.connect(on_text_changed)
            
            self.table.setCellWidget(r_idx, 8, comment_container)

            # 9. Review Widget (New)
            # Similar to Comment but saves to 'review' key
            # Also supports Tags? User said "Like Comment, can add Tag".
            # Can we reuse the same tags list? Or separate review tags?
            # User said "Same as Comment column". Usually implies one "Tags" list per record.
            # So adding a tag in Review adds it to the record's global tags.
            
            review_container = QWidget()
            review_container.setMaximumHeight(180)  # 防止佈局超出版面
            review_layout = QVBoxLayout(review_container)
            review_layout.setContentsMargins(4, 4, 4, 4)
            review_layout.setSpacing(2)
            
            rev_edit = TagCommentEdit()
            rev_edit.setPlaceholderText("輸入檢討（自動儲存）...")
            rev_edit.setPlainText(r.get("review", ""))
            
            # Connect Tag Add Button (Same Tag List)
            rev_edit.add_tag_clicked.connect(
                lambda rec=r, container=review_container: self._show_tag_menu(rec, container)
            )
            
            review_layout.addWidget(rev_edit, 1)
            
            # Review Tag Display (Same display logic?)
            # If we already show tags in Col 7, showing them in Col 8 is redundant but consistent.
            # User asked for "Same as Comment".
            # Let's show tags here too if they exist.
            r_tags = r.get("tags", [])
            if r_tags:
                r_tag_container = QWidget()
                r_tag_container.setObjectName('tag_container')
                r_tag_layout = FlowLayout(r_tag_container, margin=2, hSpacing=4, vSpacing=4)
                
                for tag_id in r_tags:
                    # Avoid duplicated visual if needed, but per-widget is safer
                    badge = self._create_tag_badge(tag_id, r, removable=True)
                    if badge:
                        r_tag_layout.addWidget(badge)
                
                review_layout.addWidget(r_tag_container)
                
            # Review Auto-Save
            rev_save_timer = QTimer(rev_edit) # Parented
            rev_save_timer.setSingleShot(True)
            rev_save_timer.setInterval(500)
            
            def save_review(text_edit=rev_edit, record=r):
                new_rev = text_edit.toPlainText()
                print(f"DEBUG: Auto-saving review for {record['code']}: '{new_rev[:10]}...'")
                self.manager.update_record(record['date'], record['code'], review=new_rev)
            
            rev_save_timer.timeout.connect(save_review)
            
            def on_rev_changed(current_code=r['code'], timer=rev_save_timer):
                timer.start()
            
            rev_edit.textChanged.connect(on_rev_changed)
            
            self.table.setCellWidget(r_idx, 9, review_container)
        
        self._update_pager_ui()

    def sort_items(self):
        self.current_page = 0  # 排序後回第一頁
        self.load_items()

    def _render_trigger_cell(self, trigger_info, record_date_str=None, stock_code=None):
        from PyQt6.QtWidgets import QLabel
        from core.utils import DateUtils
        from datetime import datetime, timedelta
        import json
        
        # 1. Determine Start/End Date based on Record Date
        max_date = None
        if record_date_str:
            try:
                max_date = datetime.strptime(record_date_str, "%Y-%m-%d")
            except:
                max_date = datetime.now()
        
        if not max_date:
             return QLabel("-")

        # [Logic] Lookback 9 Trading Days from Record Date (inclusive)
        # Find T-9
        cutoff_date = max_date
        found_trading_days = 0
        temp_curr = max_date
        
        for _ in range(30): 
            temp_curr -= timedelta(days=1)
            if DateUtils.is_trading_day(temp_curr):
                found_trading_days += 1
                if found_trading_days == 9:
                    cutoff_date = temp_curr
                    break
        
        # 不論最早觸發在哪一天，強制一律顯示近 9 個交易日 (從 cutoff_date 到 max_date)
        min_date = cutoff_date
        
        # 2. Fetch from Database using stock_code
        parsed_dates = []
        has_db_data = False
        if stock_code:
            try:
                if not hasattr(self, 'disposal_db'):
                    from core.disposal_database import DisposalDatabase
                    self.disposal_db = DisposalDatabase()
                
                from core.database_clause_ext import get_attention_clauses
                from collections import defaultdict
                
                start_str = cutoff_date.strftime("%Y-%m-%d")
                end_str = max_date.strftime("%Y-%m-%d")
                
                clauses_list = get_attention_clauses(self.disposal_db.conn, start_str, end_str, stock_code)
                
                if clauses_list:
                    has_db_data = True
                    date_clauses = defaultdict(list)
                    num_to_chinese = {
                        1: '一', 2: '二', 3: '三', 4: '四',
                        5: '五', 6: '六', 7: '七', 8: '八'
                    }
                    for rec in clauses_list:
                        d_str = rec['announce_date']
                        c_num = rec['clause_number']
                        if c_num in num_to_chinese:
                            date_clauses[d_str].append(num_to_chinese[c_num])
                        else:
                            date_clauses[d_str].append(str(c_num))
                            
                    for d_str, c_list in date_clauses.items():
                        # 去除重複條款並排序
                        unique_clauses = []
                        for c in c_list:
                            if c not in unique_clauses:
                                unique_clauses.append(c)
                        d_obj = datetime.strptime(d_str, "%Y-%m-%d")
                        parsed_dates.append((d_obj, ",".join(unique_clauses)))
            except Exception as e:
                print(f"Error fetching clauses for {stock_code}: {e}")

        # 3. Fallback to trigger_info JSON or agg_data if missing current date
        has_max_date = any(dt_obj.date() == max_date.date() for dt_obj, _ in parsed_dates)
        if not has_db_data or not has_max_date:
            raw_map = {}
            if isinstance(trigger_info, dict):
                raw_map = trigger_info
            elif isinstance(trigger_info, str):
                try:
                    clean_json = trigger_info.replace("'", '"')
                    raw_map = json.loads(clean_json)
                except:
                    pass
            
            # [Fix] Fallback to agg_data if trigger_info didn't provide today's info
            has_today_in_raw = False
            if raw_map:
                m_str = f"{max_date.month:02d}/{max_date.day:02d}"
                m_str2 = f"{max_date.month}/{max_date.day}"
                has_today_in_raw = (m_str in raw_map) or (m_str2 in raw_map)

            # [Fix] 總是嘗試從 agg_data 獲取真實條款，以覆蓋「注意」這種雜訊
            if record_date_str and hasattr(self, 'cache_manager') and self.cache_manager:
                try:
                    agg_date_str = record_date_str.replace("-", "")
                    agg_data = self.cache_manager.get_agg_data(agg_date_str)
                    if agg_data and str(stock_code) in agg_data:
                        agg_clauses = agg_data[str(stock_code)].get("clauses", {})
                        if isinstance(agg_clauses, dict):
                            for ak, av in agg_clauses.items():
                                av_str = str(av).strip()
                                # 覆蓋條件：如果是真實數值條款（不是空、注意、連續等），就覆蓋
                                if av_str and "注意" not in av_str and "連續" not in av_str and "詳見" not in av_str:
                                    raw_map[ak] = av_str
                                elif ak not in raw_map:
                                    raw_map[ak] = av_str
                except Exception as e:
                    print(f"Fallback to agg_data for clauses failed: {e}")
            
            now = datetime.now()
            current_year = now.year
            current_month = now.month
            
            existing_dates = {d.date() for d, _ in parsed_dates}
            
            for k, v in raw_map.items():
                try:
                    parts = k.split("/")
                    m = int(parts[0])
                    d = int(parts[1])
                    y = current_year
                    if m > 6 and current_month <= 6:
                        y -= 1
                    elif m < 6 and current_month >= 9:
                        y += 1
                    
                    dt_obj = datetime(y, m, d)
                    if dt_obj.date() not in existing_dates:
                        parsed_dates.append((dt_obj, str(v)))
                except:
                    continue

        parsed_dates.sort(key=lambda x: x[0])

        # 4. Gap Filling (Store ALL values including empty strings)
        # 固定顯示 8 個交易日，不要根據最早出現的條款去縮短表格
        min_date = cutoff_date

        final_list = []
        curr = min_date
        
        lookup_map = {}
        for dt_obj, val in parsed_dates:
            k1 = dt_obj.strftime("%m/%d")
            k2 = f"{dt_obj.month}/{dt_obj.day}"
            # Store ALL values (including empty strings from trigger_info)
            # We need to preserve empty strings to differentiate from missing dates
            lookup_map[k1] = val
            lookup_map[k2] = val

        # Safety check to prevent infinite loop
        if curr > max_date:
             curr = max_date
             
        while curr <= max_date:
            if DateUtils.is_trading_day(curr):
                k1 = curr.strftime("%m/%d")
                k2 = f"{curr.month}/{curr.day}"
                val = lookup_map.get(k1, lookup_map.get(k2, None))
                
                # Display "-" for None or empty string
                if val is None or (isinstance(val, str) and val.strip() == ""):
                    val = "-"
                
                final_list.append((curr, val))
            curr += timedelta(days=1)
            
        # [Limit] Max 12 days (Safe buffer)
        if len(final_list) > 12:
            final_list = final_list[-12:]

        # 5. HTML Generation (分兩列，每列最多 5 天)
        html = "<table style='border-collapse: collapse; text-align: center; background-color: #1E1E1E; width: 100%;'>"
        
        for i in range(0, len(final_list), 5):
            chunk = final_list[i:i+5]
            date_row = "<tr>"
            clause_row = "<tr>"
            
            for dt_obj, val in chunk:
                d_str = dt_obj.strftime("%m/%d")
                date_row += f"<th style='border: 1px solid #555; padding: 4px; color: #BBB; font-size: 11px;'>{d_str}</th>"
                
                v_str = str(val).strip()
                if v_str and v_str not in ["-", "—", "–", ""]:
                    html_parts = []
                    clauses = v_str.split(",")
                    for c in clauses:
                        c = c.strip()
                        if not c:
                            continue
                            
                        # Replace wordy clauses with short Chinese numerals
                        c = c.replace("第一款", "一").replace("第二款", "二") \
                             .replace("第三款", "三").replace("第四款", "四") \
                             .replace("第五款", "五").replace("第六款", "六") \
                             .replace("第七款", "七").replace("第八款", "八")
                        
                        # Filter out noise identical to dashboard's data merger
                        if "連續" in c or c == "詳見紀錄" or c == "注意":
                            continue
                        
                        if c == "一" or "一" in c:
                            html_parts.append(f"<span style='color: #FF4444;'>{c}</span>")
                        else:
                            html_parts.append(f"<span style='color: #4da6ff;'>{c}</span>")
                    
                    if html_parts:
                        formatted_v = ",".join(html_parts)
                        clause_row += f"<td style='border: 1px solid #555; padding: 6px; font-weight: bold; font-size: 12px;'>{formatted_v}</td>"
                    else:
                        color = "#555"
                        clause_row += f"<td style='border: 1px solid #555; padding: 6px; font-weight: bold; font-size: 12px; color: {color};'>-</td>"
                else:
                    color = "#555"  # Default gray for empty/dash
                    clause_row += f"<td style='border: 1px solid #555; padding: 6px; font-weight: bold; font-size: 12px; color: {color};'>-</td>"
            
            date_row += "</tr>"
            clause_row += "</tr>"
            html += date_row + clause_row
        
        html += "</table>"
        
        # [New] 在聽牌日如果已經處於處置中，則在表格下方標明處置頻率
        active_freq_text = ""
        print(f"[Debug] 正在檢查處置狀態: {stock_code} - {max_date}")
        if hasattr(self, 'disposal_db') and stock_code and max_date:
            act_recs = self.disposal_db.get_active_disposals(max_date.strftime("%Y-%m-%d"))
            act_recs = [r for r in act_recs if str(r.get('code')) == str(stock_code)]
            raw_freq = ""
            if act_recs:
                def safe_date(d): return d if d else '0000-00-00'
                act_recs.sort(key=lambda x: (safe_date(x.get('announce_date')), safe_date(x.get('period_start'))), reverse=True)
                raw_freq = act_recs[0].get('measure') or ""
            else:
                # [Fix] Fallback to cache (for T day newly entered disposal)
                if hasattr(self, 'cache_manager'):
                    agg_date_str = max_date.strftime("%Y%m%d")
                    agg_data = self.cache_manager.get_agg_data(agg_date_str)
                    if agg_data and str(stock_code) in agg_data:
                        ad_rec = agg_data[str(stock_code)]
                        if ad_rec.get("is_disposed", False):
                            raw_freq = ad_rec.get("measure") or ""
            
            if raw_freq:
                # 替換中文數字為阿拉伯數字，讓正則表達式順利捕捉
                for zh, ar in {"四十五": "45", "二十五": "25", "二十": "20", "五": "5", "十": "10"}.items():
                    raw_freq = raw_freq.replace(zh, ar)
                    
                import re
                m = re.search(r'(\d+)\s*分鐘', raw_freq)
                if m:
                    active_freq_text = f"處置中 {m.group(1)}分"
                elif "人工" in raw_freq or "分盤" in raw_freq:
                    active_freq_text = "處置中 停盤/其他"
                elif raw_freq:
                    active_freq_text = f"處置中 {raw_freq}"

        if active_freq_text:
            html += f"<div style='margin-top: 5px; text-align: center; color: #ff9900; font-size: 11px; font-weight: bold;'>目前狀態：{active_freq_text}</div>"
        
        lbl = QLabel()
        lbl.setText(html)
        return lbl

    def _update_tag_filter(self):
        """更新 Tag 過濾器選項"""
        current_selection = self.tag_filter_combo.currentData()
        
        # 暫停訊號，避免清空和新增項目時觸發 currentIndexChanged 導致無窮迴圈
        self.tag_filter_combo.blockSignals(True)
        
        # 清空並重新載入
        self.tag_filter_combo.clear()
        self.tag_filter_combo.addItem("所有標籤", None)
        
        # 取得所有已使用的 Tag
        used_tag_ids = self.manager.get_all_used_tags()
        
        for tag_id in used_tag_ids:
            tag_info = self.tag_manager.get_tag(tag_id)
            if tag_info:
                icon_symbol = self.tag_manager.get_icon_symbol(tag_info["icon"])
                self.tag_filter_combo.addItem(f"{icon_symbol} {tag_info['name']}", tag_id)
        
        # 恢復選擇（如果還存在）
        if current_selection:
            index = self.tag_filter_combo.findData(current_selection)
            if index >= 0:
                self.tag_filter_combo.setCurrentIndex(index)
                
        # 恢復訊號
        self.tag_filter_combo.blockSignals(False)
    
    def _create_tag_badge(self, tag_id, record, removable=False):
        """建立 Tag Badge 組件"""
        tag_info = self.tag_manager.get_tag(tag_id)
        if not tag_info:
            return None
        
        name = tag_info["name"]
        color = tag_info["color"]
        icon_key = tag_info["icon"]
        icon_symbol = self.tag_manager.get_icon_symbol(icon_key)
        
        # 使用 QLabel 作為 Badge（可點擊）
        badge = QLabel(f"{icon_symbol} {name}")
        badge.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                color: white;
                border: none;
                border-radius: 12px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: bold;
            }}
        """)
        badge.setFixedHeight(24)
        
        # 如果可移除，懸停時顯示刪除圖示
        if removable:
            badge.setMouseTracking(True)
            badge.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            
            # 重寫事件
            def enter_event(event, lbl=badge, original_text=f"{icon_symbol} {name}"):
                lbl.setText(f"{original_text} ✕")
            
            def leave_event(event, lbl=badge, original_text=f"{icon_symbol} {name}"):
                lbl.setText(original_text)
            
            def mouse_press_event(event, lbl=badge, t=tag_id, rec=record):
                # 先取得外部 container（badge -> tag_container -> comment_container）
                # 必須在 setParent(None) 之前取得，否則 parent() 會變 None
                tag_container = lbl.parent()
                outer_container = tag_container.parent() if tag_container else None
                
                # 從資料中移除 Tag（更新記憶體 + 存檔）
                self._remove_tag(rec, t)
                
                # 重建整個 tag 顯示區域（強制 Qt 完整刷新儲存格）
                if outer_container and outer_container.layout():
                    self._rebuild_tags_in_container(outer_container, rec)
            
            badge.enterEvent = enter_event
            badge.leaveEvent = leave_event
            badge.mousePressEvent = mouse_press_event
        
        return badge
    
    def _show_tag_menu(self, record, tag_scroll_widget):
        """顯示 Tag 選擇選單"""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #252526;
                color: white;
                border: 1px solid #4da6ff;
            }
            QMenu::item:selected {
                background-color: #4da6ff;
            }
        """)
        
        all_tags = self.tag_manager.get_tag_list()
        current_tags = record.get("tags", [])
        
        if not all_tags:
            no_tag_action = menu.addAction("無可用標籤")
            no_tag_action.setEnabled(False)
        else:
            for tag in all_tags:
                tag_id = tag["id"]
                name = tag["name"]
                icon_symbol = self.tag_manager.get_icon_symbol(tag["icon"])
                
                action = menu.addAction(f"{icon_symbol}  {name}")
                
                # 已選中的 Tag 顯示勾選標記
                if tag_id in current_tags:
                    action.setCheckable(True)
                    action.setChecked(True)
                    action.setEnabled(False)  # 已選中的不可再選
                else:
                    action.triggered.connect(lambda checked, tid=tag_id, rec=record, scroll=tag_scroll_widget: self._add_tag(rec, tid, scroll))
        
        # 在按鈕下方顯示選單
        menu.exec(QCursor.pos())
    
    def _rebuild_tags_in_container(self, container, record):
        """完整重建 Tag 顯示區域，強制 Qt 刷新儲存格"""
        layout = container.layout()
        if not layout:
            return
        
        # 1. 移除並刪除舊的 tag_container
        for i in range(layout.count() - 1, -1, -1):
            item = layout.itemAt(i)
            widget = item.widget() if item else None
            if widget and widget.objectName() == 'tag_container':
                layout.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
                break
        
        # 2. 重建 tag_container（不管是否有 tags，都建立容器）
        tags = record.get('tags', [])
        tag_container = QWidget()
        tag_container.setObjectName('tag_container')
        tag_layout = FlowLayout(tag_container, margin=2, hSpacing=4, vSpacing=4)
        
        for tag_id in tags:
            badge = self._create_tag_badge(tag_id, record, removable=True)
            if badge:
                tag_layout.addWidget(badge)
        
        layout.addWidget(tag_container)
        tag_container.show()
        
        # 3. 強制整個容器重新繪製（不能用 adjustSize，那會縮小 container 寬度）
        QApplication.processEvents()  # 讓 Qt 處理所有待定事件（包含佈局更新）
        container.repaint()
        if hasattr(self, 'table') and self.table:
            self.table.viewport().repaint()

    def _add_tag(self, record, tag_id, tag_scroll_widget):
        """將 Tag 新增到紀錄"""
        current_tags = record.get("tags", [])
        if tag_id not in current_tags:
            current_tags.append(tag_id)
            record["tags"] = current_tags
            self.manager.update_record(record["date"], record["code"], tags=current_tags)
            
        # 動態重建 Tag 顯示區域（確保 Qt 完整刷新儲存格）
        self._rebuild_tags_in_container(tag_scroll_widget, record)
        
        # 更新 Tag 過濾器（不會觸發 reload）
        self._update_tag_filter()
    
    def _remove_tag(self, record, tag_id):
        """從紀錄中移除 Tag"""
        current_tags = record.get("tags", [])
        if tag_id in current_tags:
            current_tags.remove(tag_id)
            record["tags"] = current_tags
            self.manager.update_record(record["date"], record["code"], tags=current_tags)
            
            # 動態移除 badge（不 reload 整個表格）
            # Note: badge本身有刪除功能，會傳遞自己的 widget
            # 此處預留參數，實際由 badge 的 mousePressEvent 處理
            
            # 更新 Tag 過濾器
            self._update_tag_filter()
    def filter_items(self):
        """過濾項目（支援搜尋文字和 Tag）— 分頁版本"""
        self.current_page = 0  # 搜尋條件改變時回第一頁
        self.load_items()

