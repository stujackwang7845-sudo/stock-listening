
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
from core.dashboard_engine import (
    build_agg_data, merge_disposal_status_from_db, build_local_agg_data,
    merge_clauses_from_listening_history, merge_clauses_from_db, compute_dashboard_rows,
    compute_info_boxes,
)

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

    def __init__(self, target_date, db_path=None):
        super().__init__()
        self.target_date = target_date
        from core.runtime import get_paths
        self.db_path = db_path or get_paths().disposal_db

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
        # 計算本體在 core/dashboard_engine.py(build_agg_data)，這裡只負責執行緒與訊號
        agg_data = build_agg_data(self.history_manager, target_date=self.target_date,
                                  cache_mgr=self.cache_mgr, progress=self.progress_update.emit)
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
            
            self.calc_worker = CalculationWorker(code, source, stock_name=stock_name, target_date=self.current_display_date,
                                                 agg_entry=getattr(self, 'agg_data', {}).get(code))
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
            # [2026-10-04] 排除條件改由 conditions_engine 產出(已含「排除條件:」標題)，跟總覽同一份
            if excl_lines:
                 html_content += "<br>" + "<br>".join(excl_lines)
            else:
                 html_content += "<br><br><div style='color: #FF4444; font-weight: bold; margin-bottom: 5px; font-size: 20px;'>排除條件:</div>"
                 html_content += "<div style='color: #888888;'>無</div>"
            
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
        """Refactored Info Box Update Logic(計算本體在 core/dashboard_engine.compute_info_boxes)"""
        boxes = compute_info_boxes(agg_data, self.current_display_date, self.history_manager, self.mf_db, self.cb_db)
        self.today_attention_list = boxes["today_attention_list"]
        self.today_attention_map = boxes["today_attention_map"]
        self.today_attention_names = boxes["today_attention_names"]
        obs_list, notice_list = boxes["obs_list"], boxes["notice_list"]
        active_list, exit_list = boxes["active_list"], boxes["exit_list"]

        self.observer_box.update_items(obs_list)
        self.disposition_box.update_items(notice_list)
        
        if hasattr(self, "disp_active_box"):
             self.disp_active_box.update_items(active_list)
        
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

        # 每列要顯示的值由 core/dashboard_engine.compute_dashboard_rows 算好(2026-10-04 P2 搬出)，
        # 這裡只依結果建 Qt 元件。
        result = compute_dashboard_rows(
            agg_data, self.current_display_date, self.calendar, self.history_manager,
            self.today_attention_map, self.today_attention_names, self.mf_db, self.cb_db,
        )
        headers = result["headers"]
        rows = result["rows"]

        
        self.grid_table.setColumnCount(len(headers))
        self.grid_table.setHorizontalHeaderLabels(headers)
        self.grid_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        
        # Reset visibility for all columns first
        for i in range(len(headers)):
            self.grid_table.setColumnHidden(i, False)
            
        self.grid_table.setColumnHidden(len(headers)-1, True)
        
        self.grid_table.setRowCount(len(rows))

        for row, r in enumerate(rows):
            code, data, warning_msg, prob, escape_line = r["code"], r["data"], r["warning_msg"], r["prob"], r["escape_line"]
            is_disposed = r["is_disposed"]
            
            # Manual Alternating Background
            # Manual Alternating Background
            base_bg = QColor("#2A2A2A") if row % 2 == 1 else QColor("#1E1E1E")
            
            tooltip_txt = r["tooltip"]

            # Determine Row Highlight:
            # 1. Pink if Disposed (含當天剛公告、處置期間尚未開始者 —
            #    [Fix 2026-08-31] 使用者要求不再區分「已生效/剛公告」兩種顏色，
            #    公告當天就統一顯示粉紅色 + 處置頻率)
            # 2. Light Blue if Listening (min_needed == 1) - User Request

            is_listening = r["is_listening"]

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
            source_raw = r["source"]
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
            freq_text = r["freq_text"]
            measure = r["freq_tip"]

            freq_item = QTableWidgetItem(freq_text)
            freq_item.setBackground(QBrush(base_bg))
            if freq_text:
                freq_item.setForeground(QBrush(QColor("#FFCC00"))) # Gold
                freq_item.setToolTip(measure) # Show full measure on hover
            else:
                freq_item.setForeground(QBrush(QColor("#CCCCCC")))
            freq_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid_table.setItem(row, 3, freq_item)

            days_text = r["days_text"]

            days_item = QTableWidgetItem(days_text)
            days_item.setBackground(QBrush(base_bg))
            if days_text:
                days_item.setForeground(QBrush(QColor("#FFCC00")))
            else:
                days_item.setForeground(QBrush(QColor("#CCCCCC")))
            days_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid_table.setItem(row, 4, days_item)

            # Short Selling Status (Col 5) - 改從 mf_db 讀取
            can_short = r["can_short"]
            short_item = QTableWidgetItem("可" if can_short else "")
            short_item.setBackground(QBrush(base_bg))
            if can_short:
                short_item.setForeground(QBrush(QColor("#44FF44")))
                short_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                 short_item.setForeground(QBrush(QColor("#CCCCCC")))
            self.grid_table.setItem(row, 5, short_item)

            # Futures Status (Col 6) - 改從 mf_db 讀取
            has_futures = r["has_futures"]
            futures_item = QTableWidgetItem("有" if has_futures else "")
            futures_item.setBackground(QBrush(base_bg))
            if has_futures:
                 futures_item.setForeground(QBrush(QColor("#4da6ff")))
                 futures_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                 futures_item.setForeground(QBrush(QColor("#CCCCCC")))
            self.grid_table.setItem(row, 6, futures_item)

            # CB Status (Col 7)
            has_cb = r["has_cb"]
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
            
            for cell in r["date_cells"]:
                d = cell["date"]
                date_col_map[d] = col_idx # Store index
                
                final_html = cell["html"]
                lbl = QLabel(final_html)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                # Set background to match row
                lbl.setStyleSheet(f"QLabel {{ background-color: {bg_hex}; border: none; font-size: 14px; }}")
                
                # Add Sort Item (Hidden value for sorting)
                sort_val = cell["sort"]
                
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
            target_date = r["highlight_date"]
            if target_date is not None:
                
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
            
            final_msg, is_official = r["final_msg"], r["is_official"]

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
        """處置狀態以 disposal_records 為準合併進 agg_data(本體在 core/dashboard_engine.py)"""
        return merge_disposal_status_from_db(agg_data, target_date, self.history_manager)

    def _build_local_agg_data(self, target_date):
        """無快取時從 History + Disposal DB 重建 agg_data(本體在 core/dashboard_engine.py)"""
        return build_local_agg_data(target_date, self.history_manager)

    def _merge_clauses_from_listening_history(self, agg_data, target_date):
        """本體在 core/dashboard_engine.py"""
        return merge_clauses_from_listening_history(agg_data, target_date, self.history_manager)

    def _merge_clauses_from_db(self, agg_data, target_date):
        """attention_clauses 過去 30 個交易日的條款合併進 agg_data(本體在 core/dashboard_engine.py)"""
        return merge_clauses_from_db(agg_data, target_date)


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
    
    def __init__(self, code, source, stock_name=None, target_date=None, agg_entry=None):
        super().__init__()
        self.code = code
        self.source = source
        self.stock_name = stock_name
        self.target_date = target_date
        self.agg_entry = agg_entry
        
    def run(self):
        # [Fix 2026-10-04] 改呼叫 core/conditions_engine.compute_stock_conditions，跟處置預測總覽
        # 同一支函式、同一份資料(attention_clauses 逐日條款 + 處置紀錄 + 官方聽牌原因)。
        # 舊寫法逐日即時查 API 自己湊還差次數：查不到就算錯，且「30日內曾有第一款」就把
        # 第一款硬設成還差1次(第一款要連續3日才進處置)，跟總覽結果對不起來。
        from core.conditions_engine import compute_stock_conditions
        try:
            anchor = self.target_date if self.target_date else DateUtils.get_last_trading_day()
            anchor_dt = dt.datetime(anchor.year, anchor.month, anchor.day)

            disp_records = []
            try:
                ddb = DisposalDatabase()
                disp_records = ddb.get_disposal_by_code(self.code)
                ddb.close()
            except Exception as e:
                print(f"[CalcWorker] {self.code} 讀處置紀錄失敗: {e}")

            official_reason = ""
            try:
                for r in HistoryManager().get_listening_data(anchor_dt):
                    if str(r.get("code")) == self.code:
                        official_reason = str(r.get("official_reason") or r.get("trigger_info", ""))
                        break
            except Exception as e:
                print(f"[CalcWorker] {self.code} 讀官方聽牌原因失敗: {e}")

            cond = compute_stock_conditions(
                self.code, self.source, self.stock_name, anchor_dt,
                clauses_map=(self.agg_entry or {}).get("clauses") or {},
                disp_records=disp_records,
                official_reason=official_reason,
            )
            lines, excl_lines = cond["lines"], cond["exclusion_lines"]
        except Exception as e:
            import traceback
            traceback.print_exc()
            lines, excl_lines = [f"計算錯誤: {str(e)[:40]}"], []

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