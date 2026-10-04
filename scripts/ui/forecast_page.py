"""
處置預測總覽頁面
分三個區塊：聽牌(官方) → 一進聽(預測) → 處置中
與 Dashboard 觀察區使用完全相同的判斷邏輯。
"""
import re, math, json
from datetime import datetime, timedelta
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QLabel, QLineEdit, QPushButton, QProgressBar,
    QFrame, QSizePolicy, QCalendarWidget, QDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont
from core.utils import DateUtils
from core.cache import CacheManager
from core.predictor import DispositionPredictor
from core.forecast_engine import (
    parse_frequency, predict_exact_disposal_frequency, guess_reason,
    build_forecast_inputs, run_forecast,
)

class SelectableExpandLabel(QLabel):
    def __init__(self, text, table, row, page):
        super().__init__(text)
        self.table = table
        self.row = row
        self.page = page

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if not self.hasSelectedText():
            self.page._on_cell_clicked(self.row, 10, self.table)

class ForecastWorker(QThread):
    """非同步載入預測資料(計算本體在 core/forecast_engine.py，這裡只負責執行緒與進度訊號)"""
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(dict)

    # dashboard.py、disposal_stats_page.py 透過這兩個名稱呼叫，保留別名
    _parse_frequency = staticmethod(parse_frequency)
    _predict_exact_disposal_frequency = staticmethod(predict_exact_disposal_frequency)

    def __init__(self, agg_data, date_str, attention_list, mf_db, cb_db, punish_df=None, is_history=False):
        super().__init__()
        self.agg_data = agg_data
        self.date_str = date_str
        self.attention_list = attention_list or []
        self.mf_db = mf_db
        self.cb_db = cb_db
        self.punish_df = punish_df  # Shioaji api.punish() 結果
        self.is_history = is_history

    def run(self):
        result = run_forecast(
            self.agg_data, self.date_str, self.attention_list, self.mf_db, self.cb_db,
            punish_df=self.punish_df, is_history=self.is_history, progress=self.progress.emit,
        )
        self.finished.emit(result)

    def _guess_reason(self, data):
        return guess_reason(data)



class ForecastPage(QWidget):
    """處置預測總覽頁面"""
    status_message_updated = pyqtSignal(str)
    initial_load_finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.cache_manager = CacheManager()
        self.mf_db = None
        self.cb_db = None
        try:
            from core.margin_futures_db import MarginFuturesDatabase
            self.mf_db = MarginFuturesDatabase()
        except: pass
        try:
            from core.cb_data import CBDatabase
            self.cb_db = CBDatabase()
        except: pass

        self.worker = None
        self.is_loaded = False
        self.current_display_date = DateUtils.get_last_trading_day()
        self.init_ui()

    def showEvent(self, event):
        if not self.is_loaded:
            self.load_data()
            self.is_loaded = True
        super().showEvent(event)

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        # 工具列
        toolbar = QHBoxLayout()
        
        btn_style = """
            QPushButton {
                background-color: #333333; color: #FFFFFF;
                border: 1px solid #555555; border-radius: 4px;
                padding: 3px 8px; font-weight: bold;
            }
            QPushButton:hover { background-color: #444444; }
        """
        self.btn_prev = QPushButton("<")
        self.btn_prev.setFixedSize(30, 26)
        self.btn_prev.setStyleSheet(btn_style)
        self.btn_prev.clicked.connect(lambda: self.change_date(-1))
        toolbar.addWidget(self.btn_prev)
        
        self.date_btn = QPushButton("載入中...")
        self.date_btn.setFixedSize(120, 26)
        self.date_btn.setStyleSheet("""
            QPushButton {
                background-color: #1E1E1E; color: #4da6ff;
                border: 1px solid #4da6ff; border-radius: 4px;
                font-weight: bold; font-size: 14px;
            }
        """)
        self.date_btn.clicked.connect(self.toggle_calendar)
        toolbar.addWidget(self.date_btn)
        
        self.btn_next = QPushButton(">")
        self.btn_next.setFixedSize(30, 26)
        self.btn_next.setStyleSheet(btn_style)
        self.btn_next.clicked.connect(lambda: self.change_date(1))
        toolbar.addWidget(self.btn_next)
        
        toolbar.addSpacing(15)
        toolbar.addWidget(QLabel("搜尋:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("股號/股名...")
        self.search_input.setFixedWidth(150)
        self.search_input.textChanged.connect(self._apply_search)
        toolbar.addWidget(self.search_input)
        toolbar.addSpacing(10)
        self.refresh_btn = QPushButton("🔄 重新載入")
        self.refresh_btn.clicked.connect(self.reload_data)
        toolbar.addWidget(self.refresh_btn)
        self.stats_lbl = QLabel("")
        self.stats_lbl.setStyleSheet("color:#888;")
        toolbar.addWidget(self.stats_lbl)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 進度條
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximumHeight(6)
        layout.addWidget(self.progress_bar)

        # === 外層 ScrollArea (整頁可捲動，但每個表格展開全部列) ===
        from PyQt6.QtWidgets import QScrollArea
        from PyQt6.QtCore import pyqtSignal
        
        class ClickableLabel(QLabel):
            clicked = pyqtSignal()
            def mousePressEvent(self, event):
                if event.button() == Qt.MouseButton.LeftButton:
                    self.clicked.emit()
                super().mousePressEvent(event)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        content = QVBoxLayout(container)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(6)

        # 聽牌區
        # official_set 來自 history_manager 的聽牌名單，資料源頭是 TWSE notetrans
        # + TPEx bulletin/warning——官方「累積注意次數已達/接近處置標準」正式清單，
        # 「聽牌」用詞是對的。真正需要留意的是這份清單依賴 attention_clauses 表
        # 保持最新(見 dashboard.py 的 auto_refresh_clauses_on_startup)，否則本地
        # 累積次數計算(min_needed)會低估，才會誤以為聽牌股也該出現在一進聽。
        self.listening_lbl = ClickableLabel("🟧 聽牌 (官方) ▼")
        self.listening_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.listening_lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#FFC850; padding:2px 0;")
        content.addWidget(self.listening_lbl)
        self.listening_table = self._create_forecast_table()
        content.addWidget(self.listening_table)
        self.listening_lbl.clicked.connect(lambda: self._toggle_section(self.listening_lbl, self.listening_table, "🟧 聽牌 (官方)"))

        # 一進聽區
        self.onestep_lbl = ClickableLabel("🔵 一進聽 (預測) ▼")
        self.onestep_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.onestep_lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#78B4FF; padding:2px 0;")
        content.addWidget(self.onestep_lbl)
        self.onestep_table = self._create_forecast_table()
        content.addWidget(self.onestep_table)
        self.onestep_lbl.clicked.connect(lambda: self._toggle_section(self.onestep_lbl, self.onestep_table, "🔵 一進聽 (預測)"))

        # 處置中區
        self.disposed_lbl = ClickableLabel("🔴 處置中 ▼")
        self.disposed_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.disposed_lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#FF7878; padding:2px 0;")
        content.addWidget(self.disposed_lbl)
        self.disposed_table = self._create_disposed_table()
        content.addWidget(self.disposed_table)
        self.disposed_lbl.clicked.connect(lambda: self._toggle_section(self.disposed_lbl, self.disposed_table, "🔴 處置中"))

        content.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

    def _create_forecast_table(self):
        """建立聽牌/一進聽表格"""
        table = QTableWidget()
        # 新增「最後回補日」於市場右側
        cols = ["代號","名稱","市場","最後回補日","目前狀態","進處置",
                "連3日第1款","連5日1-8款","10日內6次","30日內12次",
                "進處置條件"]
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(True)
        table.setWordWrap(True)
        table.cellClicked.connect(self._on_cell_clicked)

        header = table.horizontalHeader()
        for i in range(6):  # 代號/名稱/市場/最後回補日/目前狀態/進處置
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        for i in range(6, 10):  # 四大規則
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(i, 105)
        header.setSectionResizeMode(10, QHeaderView.ResizeMode.Stretch)  # 進處置條件

        # 每個表格展開全部列、關閉自身捲動條
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        table.setMinimumHeight(50)
        return table

    def _create_disposed_table(self):
        """建立處置中表格"""
        table = QTableWidget()
        cols = ["代號","名稱","市場","撮合","初犯/累犯","開始","結束","出關日","剩餘交易日","處置原因"]
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(True)

        header = table.horizontalHeader()
        for i in range(len(cols)):
            if i == len(cols) - 1:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)

        table.setWordWrap(True)
        table.cellClicked.connect(self._on_cell_clicked)

        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        table.setMinimumHeight(50)
        return table

    def _auto_height(self, table):
        """自動調整表格高度以顯示全部內容（不需要捲動）"""
        rows = table.rowCount()
        if rows == 0:
            table.setFixedHeight(50)
            return
        # 計算所有行的實際高度
        total_h = table.horizontalHeader().height() + 4
        for r in range(rows):
            total_h += table.rowHeight(r)
        table.setFixedHeight(total_h)

    def reload_data(self):
        self.is_loaded = False
        self.load_data()

    def _toggle_section(self, lbl, table, base_text):
        """控制表格收合，並更新標題的箭頭與計數"""
        is_visible = not table.isVisible()
        table.setVisible(is_visible)
        
        # 取得當前計數 (如果有)
        text = lbl.text()
        count_str = ""
        import re
        match = re.search(r'\(\d+ 檔\)', text)
        if match:
            count_str = f" {match.group(0)}"
            
        arrow = "▼" if is_visible else "▶"
        lbl.setText(f"{base_text} {arrow}{count_str}")

    def load_data(self):
        """讀取當前 current_display_date 的預測與處置資料，建構表格。"""
        if not hasattr(self, "search_input"): return
        
        date_str = self.current_display_date.strftime("%Y%m%d")
        anchor_dt = self.current_display_date
        
        # 用於防範 Race Condition 的指標
        self._expected_date = date_str

        # === 第一～三步：快取底稿 + listening_history/attention_clauses 補條款 + 處置紀錄(core/forecast_engine.py) ===
        agg_data, attention_list = build_forecast_inputs(anchor_dt)

        if not agg_data:
            self.date_btn.setText("無資料")
            self.stats_lbl.setText("⚠ 無快取且 listening_history 無此日期資料")
            self.initial_load_finished.emit()
            return

        self.date_btn.setText(anchor_dt.strftime('%Y-%m-%d'))
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.refresh_btn.setEnabled(False)

        # 嘗試從 Shioaji 取得處置清單（用於「目前狀態」欄位）
        punish_df = None
        try:
            from core.shioaji_client import ShioajiClient
            sj_client = ShioajiClient()
            punish_df = sj_client.get_punish()
            if punish_df is not None:
                print(f"[ForecastPage] Shioaji punish: {len(punish_df)} 筆處置資料")
        except Exception as e:
            print(f"[ForecastPage] Shioaji punish 取得失敗 (將使用本地資料): {e}")

        self.worker = ForecastWorker(agg_data, date_str, attention_list, self.mf_db, self.cb_db, punish_df=punish_df)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, c, t):
        if t > 0:
            self.progress_bar.setMaximum(t)
            self.progress_bar.setValue(c)
            # 發送給 Splash Screen
            self.status_message_updated.emit(f"正在分析處置預測 ({c}/{t})...")

    def _on_finished(self, result):
        self.progress_bar.setVisible(False)
        self.refresh_btn.setEnabled(True)
        self.is_loaded = True
        self._result = result
        self._populate_all(result)
        self.initial_load_finished.emit()

    def _apply_search(self):
        if hasattr(self, '_result'):
            self._populate_all(self._result)

    def _populate_all(self, result):
        s = self.search_input.text().lower()
        ls = result["listening"]
        os_ = result["one_step"]
        ds = result["disposed"]
        if s:
            ls = [r for r in ls if s in r["code"].lower() or s in r["name"].lower()]
            os_ = [r for r in os_ if s in r["code"].lower() or s in r["name"].lower()]
            ds = [r for r in ds if s in r["code"].lower() or s in r["name"].lower()]

        self.listening_lbl.setText(f"🟧 聽牌 (官方) {'▼' if self.listening_table.isVisible() else '▶'} ({len(ls)} 檔)")
        self.onestep_lbl.setText(f"🔵 一進聽 (預測) {'▼' if self.onestep_table.isVisible() else '▶'} ({len(os_)} 檔)")
        self.disposed_lbl.setText(f"🔴 處置中 {'▼' if self.disposed_table.isVisible() else '▶'} ({len(ds)} 檔)")
        self.stats_lbl.setText(f"聽牌:{len(result['listening'])} | 一進聽:{len(result['one_step'])} | 處置中:{len(result['disposed'])}")

        self._fill_forecast_table(self.listening_table, ls)
        self._fill_forecast_table(self.onestep_table, os_)
        self._fill_disposed_table(self.disposed_table, ds)

    def _fill_forecast_table(self, table, records):
        table.setSortingEnabled(False)
        table.setRowCount(len(records))
        for row, r in enumerate(records):
            self._set_item(table, row, 0, r["code"], align=Qt.AlignmentFlag.AlignCenter)
            self._set_item(table, row, 1, f"{r['name']}{r['suffix']}")
            self._set_item(table, row, 2, r["source"], align=Qt.AlignmentFlag.AlignCenter)

            # 最後回補日 (col 3)
            last_cover = r.get("last_cover_date", "")
            rem_days = r.get("cover_remaining_days", -1)
            if last_cover and rem_days >= 0:
                cover_txt = f"{last_cover}\n餘{rem_days}日"
                self._set_item(table, row, 3, cover_txt, fg=QColor(245, 158, 11), align=Qt.AlignmentFlag.AlignCenter)
            else:
                self._set_item(table, row, 3, "-", align=Qt.AlignmentFlag.AlignCenter)

            # 目前狀態 (col 4)
            status = r.get("current_status", "非處置")
            if status == "非處置":
                self._set_item(table, row, 4, status,
                               fg=QColor(80, 200, 80), align=Qt.AlignmentFlag.AlignCenter)
            else:
                self._set_item(table, row, 4, status,
                               fg=QColor(255, 100, 100), align=Qt.AlignmentFlag.AlignCenter)

            # 進處置 (col 5)
            enter_f = r.get("enter_freq", "5分")
            ef_color = QColor(255, 200, 80) if enter_f == "5分" else QColor(255, 80, 80)
            self._set_item(table, row, 5, enter_f,
                           fg=ef_color, align=Qt.AlignmentFlag.AlignCenter)

            tp = r.get("trigger_progress")
            if tp:
                self._set_blocks(table, row, 6, tp["rule1"])
                self._set_blocks(table, row, 7, tp["rule2"])
                self._set_blocks(table, row, 8, tp["rule3"])
                self._set_blocks(table, row, 9, tp["rule4"])

            # 進處置條件 — 用 HTML QLabel 顯示，支援多色粗體 (col 10)
            lines = r.get("calc_results", [])
            html_parts = []
            for line in lines:
                txt = str(line).strip()
                if not txt: continue
                # 過濾非條件行
                plain = re.sub(r'<[^>]+>', ' ', txt).strip()
                if '最新收盤' in plain: continue
                # [2026-10-04] 保留「進處置:」「達以下任一則聽牌:」分區標題，跟儀表板一致，
                # 才看得出哪些款是明天達到就進處置、哪些只是進聽牌。去掉開頭空行保持緊湊。
                if '進處置:' in plain or '達以下任一' in plain:
                    txt = re.sub(r'^(<br>)+', '', txt)
                if '無 (' in plain and '全部條件' in plain: continue
                # 保留原始 HTML (含 <b> 標記)
                html_parts.append(txt)
            
            # 第二款排除條款 (詳細格式)
            excl = r.get("exclusion_lines", [])
            if excl:
                html_parts.extend(excl)
            
            if html_parts:
                full_html = "<br>".join(html_parts)
                lbl = SelectableExpandLabel(full_html, table, row, self)
                lbl.setTextFormat(Qt.TextFormat.RichText)
                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                lbl.setWordWrap(True)
                lbl.setStyleSheet("padding:2px 4px; color:#E0E0E0; font-size:12px;")
                table.setCellWidget(row, 10, lbl)
            else:
                # 若有 calc_results 但全被過濾 → 顯示「條件未達標」
                if lines:
                    self._set_item(table, row, 10, "條件未達標",
                                   fg=QColor(120, 120, 120),
                                   align=Qt.AlignmentFlag.AlignCenter)
                else:
                    self._set_item(table, row, 10, "")

        for row in range(len(records)):
            table.setRowHeight(row, 50)

        table.setSortingEnabled(True)
        self._auto_height(table)

    def _fill_disposed_table(self, table, records):
        table.setSortingEnabled(False)
        table.setRowCount(len(records))
        for row, r in enumerate(records):
            self._set_item(table, row, 0, r["code"], align=Qt.AlignmentFlag.AlignCenter)
            self._set_item(table, row, 1, f"{r['name']}{r['suffix']}")
            self._set_item(table, row, 2, r["source"], align=Qt.AlignmentFlag.AlignCenter)
            self._set_item(table, row, 3, r.get("freq", ""), align=Qt.AlignmentFlag.AlignCenter)

            offense = ""
            enter_freq = r.get("enter_freq", "") or ""
            if "累犯" in enter_freq:
                offense = "累犯"
            elif "初犯" in enter_freq:
                offense = "初犯"
            offense_fg = QColor(255, 140, 140) if offense == "累犯" else (
                QColor(140, 200, 255) if offense == "初犯" else None)
            self._set_item(table, row, 4, offense, fg=offense_fg, align=Qt.AlignmentFlag.AlignCenter)

            self._set_item(table, row, 5, r.get("start", ""), align=Qt.AlignmentFlag.AlignCenter)
            self._set_item(table, row, 6, r.get("end", ""), align=Qt.AlignmentFlag.AlignCenter)
            self._set_item(table, row, 7, r.get("exit", ""),
                           fg=QColor(255, 100, 100), align=Qt.AlignmentFlag.AlignCenter)
            remaining = r.get("remaining", 0)
            days_elapsed = r.get("days_elapsed", 0)
            days_total = r.get("days_total", 0)
            days_text = f"{days_elapsed}/{days_total}" if days_total > 0 else ""
            if remaining <= 0:
                self._set_item(table, row, 8, days_text,
                               fg=QColor(80, 200, 80), align=Qt.AlignmentFlag.AlignCenter)
            elif remaining <= 2:
                self._set_item(table, row, 8, days_text,
                               fg=QColor(255, 180, 80), align=Qt.AlignmentFlag.AlignCenter)
            else:
                self._set_item(table, row, 8, days_text,
                               fg=QColor(120, 180, 255), align=Qt.AlignmentFlag.AlignCenter)
            self._set_item(table, row, 9, r.get("reason", ""))

        for row in range(len(records)):
            table.setRowHeight(row, 50)
            
        table.setSortingEnabled(True)
        self._auto_height(table)

    def _set_item(self, table, row, col, text, align=None, bg=None, fg=None):
        item = QTableWidgetItem(str(text))
        if align: item.setTextAlignment(align)
        if bg: item.setBackground(QBrush(bg))
        if fg: item.setForeground(QBrush(fg))
        table.setItem(row, col, item)
        return item

    def _set_blocks(self, table, row, col, rule_data):
        """
        色塊：從左到右連續顯示觸發天數(橘)，右側補灰色表示未達標部分。
        """
        current = rule_data.get("current", 0)
        target = rule_data.get("target", 3)
        needed = rule_data.get("needed", 99)

        orange = min(current, target)
        grey = target - orange
        # [Fix 2026-08-27] 原本後面接的是未封頂的 current(如 "8/3")。真正根因後來在
        # predictor.py 的 _compute_live_state() 修掉了(股票處置期間內被連續升級、
        # 同一輪從未真正出關時，每次規則觸發都要各自歸零重新算，不是只在第一次進入
        # 這段連續處置時歸零一次)，修完 current 理論上不會再超過 target。這裡的封頂
        # 純粹當防禦——萬一還有沒想到的邊界情況，也不要再顯示出「分子比分母大、
        # 長得像日期(如8/3)」這種容易誤讀的文字。
        current_display = f"{orange}+" if current > target else str(orange)
        text = "🟧" * orange + "⬜" * grey + f" {current_display}/{target}"

        if needed <= 0:
            fg = QColor(255, 80, 80)    # 已觸發 - 紅
        elif needed == 1:
            fg = QColor(255, 200, 80)   # 差1天 - 黃
        elif needed == 2:
            fg = QColor(120, 180, 255)  # 差2天 - 藍
        else:
            fg = QColor(150, 150, 150)  # 安全 - 灰

        self._set_item(table, row, col, text, fg=fg, align=Qt.AlignmentFlag.AlignCenter)

    def change_date(self, offset):
        from datetime import timedelta
        self.current_display_date += timedelta(days=offset)
        # Skip non-trading days
        while not DateUtils.is_trading_day(self.current_display_date):
            self.current_display_date += timedelta(days=1 if offset > 0 else -1)
        self.reload_data()

    def toggle_calendar(self):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QCalendarWidget, QPushButton
        from datetime import datetime
        dialog = QDialog(self)
        dialog.setWindowTitle("選擇日期")
        layout = QVBoxLayout(dialog)
        cal = QCalendarWidget()
        cal.setSelectedDate(self.current_display_date.date())
        layout.addWidget(cal)
        btn = QPushButton("確定")
        layout.addWidget(btn)
        
        def on_date_selected():
            qdate = cal.selectedDate()
            self.current_display_date = datetime(qdate.year(), qdate.month(), qdate.day())
            dialog.accept()
            self.reload_data()
            
        btn.clicked.connect(on_date_selected)
        dialog.exec()

    def _on_cell_clicked(self, row, col, table=None):
        if table is None:
            table = self.sender()
        current_height = table.rowHeight(row)
        if current_height == 50:
            table.resizeRowToContents(row)
            if table.rowHeight(row) < 50:
                table.setRowHeight(row, 50)
        else:
            table.setRowHeight(row, 50)
            
        self._auto_height(table)

    def _show_full_conditions(self, title, html):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea, QPushButton
        d = QDialog(self)
        d.setWindowTitle(title)
        d.setMinimumSize(450, 400)
        lay = QVBoxLayout(d)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        
        lbl = QLabel(html)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size: 13px; line-height: 1.5; padding: 10px;")
        scroll.setWidget(lbl)
        lay.addWidget(scroll)
        
        btn = QPushButton("關閉")
        btn.setFixedHeight(30)
        btn.clicked.connect(d.accept)
        lay.addWidget(btn)
        
        d.exec()
