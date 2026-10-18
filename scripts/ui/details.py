"""
「詳細條件」頁(2026-10-11 改版)：在總覽或儀表板的表格雙擊一列，帶入該股並跳到這頁。

上半：處置頻率、處置天數、處置期間、目前第幾天、出關日、初犯/累犯、處置條款、官方處置原因、
近期注意條款(計算在 core/disposal_detail.compute_disposal_detail，以最新公告為準)。
下半：依處置天數與初犯/累犯對應的統計圖表(新制「5天初犯」等)：平均漲跌幅、開收差幅、紅黑機率
三張圖＋明細表(畫法與「統計圖表」頁共用 ui.disposal_stats_overview 的模組函式)。
"""
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QScrollArea, QSizePolicy, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

# 歷史處置表欄名(對照「處置統計」頁的欄位代號)
_HIST_LABEL = {"-1": "處置前一天", "處置日": "處置第1天", "+1": "第2天", "+2": "第3天", "+3": "第4天",
               "+4": "第5天", "+5": "第6天", "處置結束": "處置末日", "處置期間漲跌幅": "處置期間漲跌幅",
               "出關日": "出關日", "出+1": "出關後一天"}

from ui.disposal_stats_overview import (
    DisposalStatsOverviewWorker, build_stage_figure, fill_stage_table,
)

_CARD_STYLE = "QFrame#card { background-color: #252526; border: 1px solid #3a3a3a; border-radius: 6px; }"
_KEY_STYLE = "color: #9a9a9a; font-size: 14px;"
_VAL_STYLE = "color: #E0E0E0; font-size: 15px; font-weight: bold;"
_STATE_COLOR = {"處置中": "#FF6666", "已公告，尚未生效": "#FFC850", "已出關": "#88CC88"}


class DetailsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._dataset = None          # 統計圖表資料集(第一次需要時載入，之後共用)
        self._loader = None
        self._pending = None          # 資料集載入完成後要畫的 detail
        self.init_ui()

    # ------------------------------------------------------------------ UI
    def init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        self.layout_ = QVBoxLayout(body)
        self.layout_.setContentsMargins(16, 12, 16, 16)
        self.layout_.setSpacing(12)

        self.title_label = QLabel("在「處置預測總覽」或「監控儀表板」的表格雙擊一檔股票，查看處置詳細條件")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size: 22px; font-weight: bold; color: #E0E0E0;")
        self.layout_.addWidget(self.title_label)

        # 左右兩欄：左＝處置資訊、歷史處置；右＝統計圖表標題說明、三張圖、明細表(2026-10-11 使用者要求)
        columns = QHBoxLayout()
        columns.setSpacing(14)
        self.left_ = QVBoxLayout()
        self.left_.setSpacing(12)
        self.right_ = QVBoxLayout()
        self.right_.setSpacing(10)
        columns.addLayout(self.left_, 1)
        columns.addLayout(self.right_, 1)
        self.layout_.addLayout(columns)

        # 處置資訊卡
        self.info_card = QFrame()
        self.info_card.setObjectName("card")
        self.info_card.setStyleSheet(_CARD_STYLE)
        self.info_grid = QGridLayout(self.info_card)
        self.info_grid.setContentsMargins(16, 12, 16, 12)
        self.info_grid.setHorizontalSpacing(18)
        self.info_grid.setVerticalSpacing(8)
        self.info_grid.setColumnStretch(1, 1)  # 標題欄依內容寬度，數值欄延伸
        self.info_card.setVisible(False)
        self.left_.addWidget(self.info_card)

        # 這檔的歷史處置：處置前一天 ~ 出關後一天逐日漲跌幅(與「處置統計」頁同一份計算)
        self.hist_title = QLabel("")
        self.hist_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #4da6ff;")
        self.hist_title.setVisible(False)
        self.left_.addWidget(self.hist_title)
        self.hist_table = QTableWidget()
        self.hist_table.setVisible(False)
        self.hist_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.hist_table.verticalHeader().setVisible(False)
        self.hist_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_.addWidget(self.hist_table)

        # 統計圖表
        self.stats_title = QLabel("")
        self.stats_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fcd535;")
        self.stats_title.setVisible(False)
        self.right_.addWidget(self.stats_title)
        self.stats_note = QLabel("")
        self.stats_note.setWordWrap(True)
        self.stats_note.setStyleSheet("color: #9a9a9a; font-size: 13px;")
        self.stats_note.setVisible(False)
        self.right_.addWidget(self.stats_note)
        self.charts_box = QVBoxLayout()
        self.charts_box.setSpacing(10)
        self.right_.addLayout(self.charts_box)
        self.stats_table = QTableWidget()
        self.stats_table.setVisible(False)
        self.stats_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.right_.addWidget(self.stats_table)
        self.left_.addStretch()
        self.right_.addStretch()
        self.layout_.addStretch()

    def _clear_grid(self):
        while self.info_grid.count():
            w = self.info_grid.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _clear_charts(self):
        while self.charts_box.count():
            w = self.charts_box.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _add_row(self, row, key, value, color=None, wrap=False):
        k = QLabel(key)
        k.setStyleSheet(_KEY_STYLE)
        k.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        v = QLabel(value)
        v.setStyleSheet(_VAL_STYLE + (f" color: {color};" if color else ""))
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.setWordWrap(wrap)
        self.info_grid.addWidget(k, row, 0)
        self.info_grid.addWidget(v, row, 1)

    # ------------------------------------------------------------- 資料
    def show_stock(self, code, display_date, name="", next_hint=None):
        """雙擊入口：code 股票代號、display_date 該頁目前的顯示日。"""
        from core.disposal_detail import compute_disposal_detail
        try:
            d = compute_disposal_detail(code, display_date, next_hint=next_hint)
        except Exception as e:
            self.title_label.setText(f"{code} {name} 讀取處置資料失敗：{e}")
            return
        self._render_info(d, name)
        self._render_history(code)
        self._render_stats(d)

    def _render_info(self, d, fallback_name):
        name = d.get("name") or fallback_name
        try:
            from core.futures_stocks import name_with_futures
            name = name_with_futures(d["code"], name)      # 有發行股票期貨標 (期)(2026-10-15)
        except Exception:
            pass
        self.title_label.setText(f"{name} ({d['code']})　處置詳細條件　<span style='font-size:14px;color:#9a9a9a;'>"
                                 f"顯示日 {d['display_date']}</span>")
        self._clear_grid()
        self.info_card.setVisible(True)
        if d.get("mode") == "outlook":
            self._render_outlook(d)
            return
        if not d.get("has_disposal"):
            self._add_row(0, "處置狀態", "目前沒有處置紀錄(或尚未公告)")
            self._add_next(1, d)
            self._add_clauses(2, d)
            return
        rows = [
            ("處置狀態", d["state"], _STATE_COLOR.get(d["state"])),
            ("處置頻率", f"{d['freq']}（約每{d['freq'].rstrip('分')}分鐘撮合一次）" if d["freq"].endswith("分") else d["freq"], None),
            ("處置天數", f"{d['total_days']} 個營業日", None),
            ("處置期間", f"{d['period_start']} ～ {d['period_end']}　（{d['announce_date']} 公告）", None),
            ("目前進度", "尚未開始（第0天）" if d["day_no"] == 0 else f"第{d['day_no']}天 / 共{d['total_days']}天", None),
            ("出關日", d["exit_date"], "#FF7878"),
            ("初犯 / 累犯", d["offense"] + ("　（最近30個營業日內曾發布處置）" if d["offense"] == "累犯" else ""),
             "#FF8C8C" if d["offense"] == "累犯" else "#8CC8FF"),
            ("處置條款", d["condition"], None),
            ("官方處置原因", d["official_reason"], None),
        ]
        for i, (k, v, c) in enumerate(rows):
            self._add_row(i, k, v, c, wrap=(k == "官方處置原因"))
        self._add_next(len(rows), d)
        self._add_clauses(len(rows) + 1, d)

    def _render_outlook(self, d):
        """目前沒有處置：顯示「如果接下來進處置」的預估(2026-10-16 使用者要求)。"""
        na = "不適用（尚未進處置）"
        rows = [
            ("處置狀態", d["state"], "#FFC850" if "聽牌" in d["state"] or "條件" in d["state"] else "#9a9a9a"),
            ("處置頻率", (f"{d['freq']}（約每{d['freq'].rstrip('分')}分鐘撮合一次，依下次為{d['offense']}預估）"
                       if d["freq"].endswith("分") else d["freq"]), None),
            ("處置天數", f"{d['total_days']} 個營業日（若進處置）", None),
            ("處置期間", na, "#9a9a9a"),
            ("目前進度", d["progress"], "#FF8C8C" if "聽牌" in d["progress"] or "已達" in d["progress"] else None),
            ("出關日", na, "#9a9a9a"),
            ("初犯 / 累犯", d["offense"] + ("　（最近30個營業日內曾發布處置）" if d["offense"] == "累犯" else "")
             + "　※若進處置", "#FF8C8C" if d["offense"] == "累犯" else "#8CC8FF"),
            ("天數判斷", d.get("days_note") or "—", "#9a9a9a"),
            ("上次處置", d.get("last_disposal") or "無", "#9a9a9a"),
        ]
        for i, (k, v, c) in enumerate(rows):
            self._add_row(i, k, v, c, wrap=(k in ("天數判斷", "上次處置")))
        self._add_next(len(rows), d)
        self._add_clauses(len(rows) + 1, d)

    def _add_next(self, row, d):
        """下次進處置：還差幾次注意、四條規則進度、下次是初犯還是累犯(與總覽「進處置」欄同一份)。"""
        nx = d.get("next") or {}
        tp = nx.get("trigger_progress") or {}
        if not tp:
            return
        need = tp.get("min_needed")
        rules = [("連3日第1款", "rule1"), ("連5日1-8款", "rule2"), ("10日內6次", "rule3"), ("30日內12次", "rule4")]
        prog = "、".join(f"{lbl} {tp[k]['current']}/{tp[k]['target']}" for lbl, k in rules if k in tp)
        if need == 0:
            head = "已達進處置條件" + ("（即本次已公告的處置）" if d.get("state") == "已公告，尚未生效" else "（等待公告）")
            color = "#FF8C8C"
        elif isinstance(need, int) and need <= 2:
            head = f"再 {need} 次注意就進處置 → 下次為 {nx.get('next_label', '—')}"
            color = "#FF8C8C" if "累犯" in nx.get("next_label", "") else "#FFC850"
        else:
            head = f"距離進處置還差 3 次以上（若再進處置為 {nx.get('next_label', '—')}）"
            color = None
        self._add_row(row, "下次進處置", f"{head}\n{prog}", color, wrap=True)

    def _add_clauses(self, row, d):
        """近期注意條款：儀表板式日期表(最近 10 個交易日一天一欄，沒被注意留空，顯示日標「(今)」)。"""
        days = d.get("recent_days") or []
        by_day = dict(d.get("recent_clauses") or [])
        if not days:
            self._add_row(row, "近期注意條款", "—")
            return
        k = QLabel("近期注意條款")
        k.setStyleSheet(_KEY_STYLE)
        k.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.info_grid.addWidget(k, row, 0)
        t = QTableWidget(1, len(days))
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        t.verticalHeader().setVisible(False)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setHorizontalHeaderLabels([day[5:].replace("-", "/") + (" (今)" if day == d.get("display_date") else "")
                                     for day in days])
        t.horizontalHeader().setStyleSheet("QHeaderView::section { color: #4da6ff; font-weight: bold; }")
        c13 = d.get("recent_c13") or {}
        for i, day in enumerate(days):
            c = by_day.get(day, "")
            if day in c13:
                # 第十三款(當日沖銷占比過高)：圓形圖示附在條款後面(2026-10-19 使用者要求)。
                # 那天只有第十三款時 recent_clauses 會寫「其他」，改成只顯示圖示
                from ui.badges import clause13_badge_html
                base = "" if (c == "其他" and c13[day]) else c
                color = "#9a9a9a" if base == "其他" else "#E0E0E0"
                lbl = QLabel((f"<span style='color: {color};'>{base}</span>," if base else "") + clause13_badge_html())
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet("QLabel { background: transparent; border: none; }")
                t.setItem(0, i, QTableWidgetItem(""))
                t.setCellWidget(0, i, lbl)
                continue
            it = QTableWidgetItem(c)
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            it.setForeground(QBrush(QColor("#9a9a9a" if c == "其他" else "#E0E0E0")))
            t.setItem(0, i, it)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.setRowHeight(0, 34)
        t.setFixedHeight(t.horizontalHeader().sizeHint().height() + 34 + 4)
        self.info_grid.addWidget(t, row, 1)

    def _render_history(self, code):
        from core.disposal_detail import disposal_history_rows
        try:
            h = disposal_history_rows(code)
        except Exception as e:
            self.hist_title.setText(f"歷史處置：讀取失敗 {e}")
            self.hist_title.setVisible(True)
            self.hist_table.setVisible(False)
            return
        rows, cols = h["rows"], h["columns"]
        self.hist_title.setText(f"歷史處置前後漲跌幅（新制 {len(rows)} 次，處置前一天～出關後一天的逐日漲跌；處置期間漲跌幅＝處置末日收盤÷處置前一天收盤−1）")
        self.hist_title.setVisible(True)
        if not rows:
            self.hist_table.setVisible(False)
            return
        base = ["公告日", "處置期間", "頻率", "天數", "初犯/累犯"]
        t = self.hist_table
        t.clear()
        t.setColumnCount(len(base) + len(cols))
        t.setHorizontalHeaderLabels(base + [_HIST_LABEL.get(c, c) for c in cols])
        t.setRowCount(len(rows) + 1)

        def put(r, c, text, color=None, right=False):
            it = QTableWidgetItem(text)
            if color:
                it.setForeground(QBrush(QColor(color)))
            if right:
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.setItem(r, c, it)

        for i, r in enumerate(rows):
            put(i, 0, r["announce"])
            put(i, 1, f"{r['start']} ~ {r['end']}")
            put(i, 2, r["freq"])
            put(i, 3, r["duration"])
            put(i, 4, r["offense"], "#FF8C8C" if r["offense"] == "累犯" else "#8CC8FF")
            for j, c in enumerate(cols):
                v = r["changes"].get(c)
                if isinstance(v, (int, float)):
                    put(i, len(base) + j, f"{v:+.2f}%", "#ff4d4d" if v > 0 else "#33cc66" if v < 0 else "#E0E0E0", True)
                else:
                    put(i, len(base) + j, "N/A" if v is None else str(v), "#888888", True)
        last = len(rows)
        put(last, 0, "📊 平均", "#4da6ff")
        for j, c in enumerate(cols):
            vals = [r["changes"][c] for r in rows if isinstance(r["changes"].get(c), (int, float))]
            if vals:
                m = sum(vals) / len(vals)
                put(last, len(base) + j, f"{m:+.2f}% ({len(vals)})", "#4da6ff", True)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t.setFixedHeight(t.horizontalHeader().height() + sum(t.rowHeight(i) for i in range(t.rowCount()))
                         + t.horizontalScrollBar().sizeHint().height() + 6)
        t.setVisible(True)

    def _render_stats(self, d):
        self._clear_charts()
        self.stats_table.setVisible(False)
        # 看哪一次處置的統計：規則在 core/disposal_detail.chart_choice(聽牌才看下次，處置中未聽牌看本次)
        from core.disposal_detail import chart_choice
        bucket, title, basis, heading = chart_choice(d)
        if bucket is None:
            self.stats_title.setVisible(False)
            self.stats_note.setText("沒有對應的統計分桶。")
            self.stats_note.setVisible(True)
            return
        self._stats_basis = basis
        self.stats_title.setText(f"統計圖表（{heading}）：{title}")
        self.stats_title.setVisible(True)
        if self._dataset is None:
            self.stats_note.setText("正在載入統計資料…")
            self.stats_note.setVisible(True)
            self._pending = d
            self._load_dataset()
            return
        frame = self._dataset.interval_frame(bucket)
        used, total = self._dataset.event_counts.get(bucket, (0, 0))
        self.stats_note.setText(f"{self._stats_basis}歷史上同類處置共 {total} 筆，有日K資料可統計 {used} 筆"
                                f"（統計資料產生於 {self._dataset.generated_at:%Y-%m-%d %H:%M}）。"
                                "時間點：處置前1＝處置前一個交易日，處置N＝處置第N天，出關1＝出關日。")
        self.stats_note.setVisible(True)
        if frame.empty:
            self.stats_note.setText(self.stats_note.text() + " 目前沒有樣本。")
            return
        for metric in ("return", "body", "prob"):
            canvas = FigureCanvasQTAgg(build_stage_figure(frame, metric, figsize=(7.0, 2.8)))
            canvas.setMinimumHeight(250)
            canvas.setMaximumHeight(270)  # 三張圖在右欄一個畫面內放得下
            canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.charts_box.addWidget(canvas)
        fill_stage_table(self.stats_table, frame)
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h = self.stats_table.horizontalHeader().height() + sum(
            self.stats_table.rowHeight(r) for r in range(self.stats_table.rowCount())) + 4
        self.stats_table.setFixedHeight(h)
        self.stats_table.setVisible(True)

    def _load_dataset(self):
        """跟「統計圖表」頁同一支 worker：今天已算過就讀快取，否則背景重算(不卡畫面)。"""
        if self._loader is not None and self._loader.isRunning():
            return
        self._loader = DisposalStatsOverviewWorker(force_refresh=False)
        self._loader.finished_data.connect(self._on_dataset)
        self._loader.failed.connect(lambda msg: self.stats_note.setText(f"統計資料載入失敗：{msg}"))
        self._loader.start()

    def _on_dataset(self, dataset):
        self._dataset = dataset
        if self._pending is not None:
            d, self._pending = self._pending, None
            self._render_stats(d)
