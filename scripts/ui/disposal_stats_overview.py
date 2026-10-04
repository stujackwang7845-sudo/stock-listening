"""處置股統計圖表頁。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("QtAgg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from core.disposal_stats_analytics import (
    DEFAULT_DB_PATH,
    DEFAULT_PRICE_ROOT,
    DEFAULT_REPORT_DIR,
    DURATION_OFFENSE_KEYS,
    DisposalStatsDataset,
    SUPPORTED_INTERVALS,
    build_disposal_stats_dataset,
    interval_title,
    load_cached_report_frames,
)

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei"]
plt.rcParams["axes.unicode_minus"] = False


METRICS = {
    "return": {
        "label": "平均漲跌幅",
        "column": "平均漲跌幅%",
        "color": "#fcd535",
        "axis": "平均漲跌幅 (%)",
    },
    "prob": {
        "label": "紅黑機率",
        "column": "",
        "color": "#fcd535",
        "axis": "K 線機率 (%)",
    },
    "body": {
        "label": "開收差幅",
        "column": "平均開收差幅%",
        "color": "#fcd535",
        "axis": "平均開收差幅 (%)",
    },
}

WINDOW_FILTERS: tuple[tuple[Optional[int], str], ...] = (
    (60, "60 日"),
    (120, "120 日"),
    (365, "一年"),
    (730, "二年"),
    (1095, "三年"),
    (None, "全部"),
)


class DisposalStatsOverviewWorker(QThread):
    """背景產生處置統計資料。"""

    status_message = pyqtSignal(str)
    finished_data = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        db_path: Path | str = DEFAULT_DB_PATH,
        parquet_root: Path | str = DEFAULT_PRICE_ROOT,
        report_dir: Path | str = DEFAULT_REPORT_DIR,
        force_refresh: bool = False,
        window_days: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.parquet_root = parquet_root
        self.report_dir = report_dir
        self.force_refresh = force_refresh
        self.window_days = window_days

    def run(self) -> None:
        try:
            if not self.force_refresh and self.window_days is None:
                cached = load_cached_report_frames(self.report_dir)
                # [Fix 2026-08-30] 原本只要有快取檔就直接沿用，即使是好幾天前算的
                # 舊報表，導致「統計圖表」分頁開啟軟體後永遠停在上次手動按「重新整理
                # 統計」當下的資料，不會反映今天新增的處置事件——跟 forecast_page.py/
                # dashboard.py 開啟軟體就自動抓最新資料的體驗不一致(使用者回報需要
                # 每天手動點更新)。改成只有「快取本來就是今天算的」才沿用，否則視為
                # 過期，直接重新計算一次，效果等同開啟軟體自動按一次「重新整理統計」。
                if cached is not None and cached.generated_at.date() == datetime.now().date():
                    self.status_message.emit("已載入快取統計報表")
                    self.finished_data.emit(cached)
                    return

            self.status_message.emit("正在重新計算處置統計...")
            dataset = build_disposal_stats_dataset(
                db_path=self.db_path,
                parquet_root=self.parquet_root,
                report_dir=self.report_dir,
                force_refresh=True,
                window_days=self.window_days,
            )
            self.status_message.emit("處置統計更新完成")
            self.finished_data.emit(dataset)
        except Exception as exc:
            self.failed.emit(str(exc))


class DisposalStatsOverviewWidget(QWidget):
    """獨立的處置統計圖表頁。"""

    def __init__(
        self,
        *,
        db_path: Path | str = DEFAULT_DB_PATH,
        parquet_root: Path | str = DEFAULT_PRICE_ROOT,
        report_dir: Path | str = DEFAULT_REPORT_DIR,
        auto_refresh: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.parquet_root = parquet_root
        self.report_dir = report_dir
        self.worker: Optional[DisposalStatsOverviewWorker] = None
        self.dataset: Optional[DisposalStatsDataset] = None
        self.current_metric = "return"
        self.current_interval: int | str = 5
        self.current_window_days: Optional[int] = None
        self.chart_canvas: FigureCanvasQTAgg | None = None

        self._build_ui()
        if auto_refresh:
            self.refresh(force_refresh=False)

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QWidget#statsChartRoot {
                background-color: #121212;
            }
            QFrame#statsPanel {
                background-color: #1e1e1e;
                border: 1px solid #2b2b2b;
                border-radius: 12px;
            }
            QPushButton[role="tabButton"] {
                background: transparent;
                color: #888888;
                border: none;
                border-bottom: 2px solid transparent;
                padding: 8px 4px;
                font-size: 17px;
            }
            QPushButton[role="tabButton"]:checked {
                color: #fcd535;
                border-bottom: 2px solid #fcd535;
                font-weight: bold;
            }
            QPushButton[role="filterButton"] {
                background-color: #2a2a2a;
                color: #aaaaaa;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 14px;
            }
            QPushButton[role="filterButton"]:checked {
                background-color: #444444;
                color: white;
                border-color: #555555;
            }
            QPushButton#refreshStatsButton {
                background-color: #2a2a2a;
                color: #dddddd;
                border: 1px solid #444444;
                border-radius: 6px;
                padding: 7px 14px;
            }
            QPushButton#refreshStatsButton:hover {
                border-color: #fcd535;
            }
            """
        )
        self.setObjectName("statsChartRoot")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)

        panel = QFrame()
        panel.setObjectName("statsPanel")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(16)

        top = QHBoxLayout()
        self.metric_group = QButtonGroup(self)
        self.metric_group.setExclusive(True)
        for key, meta in METRICS.items():
            button = QPushButton(meta["label"])
            button.setCheckable(True)
            button.setProperty("role", "tabButton")
            button.clicked.connect(lambda checked=False, k=key: self._set_metric(k))
            self.metric_group.addButton(button)
            top.addWidget(button)
            if key == self.current_metric:
                button.setChecked(True)

        top.addStretch()
        self.refresh_btn = QPushButton("重新整理統計")
        self.refresh_btn.setObjectName("refreshStatsButton")
        self.refresh_btn.clicked.connect(lambda: self.refresh(force_refresh=True))
        top.addWidget(self.refresh_btn)
        layout.addLayout(top)

        # [2026-08-10 修法] 新制撮合頻率統一為2分鐘，原本用來分辨 5分/20分(舊制)
        # 的按鈕已經沒有鑑別度；額外加上「2分」整體按鈕，以及依處置天數(5天/7天)
        # 與初犯/累犯細分的4個子分桶按鈕。全部塞同一行會把視窗最小寬度撐到約1300px
        # (實測過)，所以拆成兩行：第一行是母分桶(全樣本/舊制/新制全部)，
        # 第二行是新制的天數+初犯/累犯子分桶。
        #
        # [Fix 2026-09-04] 原本兩行按鈕共用同一個 QButtonGroup、全部互斥——導致
        # 點選「7天初犯」這種子分桶時，Qt 的互斥機制會自動把「2分(新制·全部)」
        # 按鈕的勾選狀態解除，畫面上看起來像是「已經離開2分新制」，但「7天初犯」
        # 本來就是2分新制底下的子集合，語意上不應該把母分桶按鈕解除選取。改成兩個
        # 獨立的 QButtonGroup：主列(全樣本/5分/20分/2分)一組，子列(天數+初犯/累犯)
        # 另一組，各自組內互斥，兩組之間不互斥。點主列按鈕時手動清掉子列的勾選
        # (代表「離開子分桶檢視」)；點子列按鈕時手動把主列的「2分(新制·全部)」
        # 按鈕設回勾選狀態(代表「這是2分新制底下的子分桶，母分桶脈絡仍然成立」)。
        self.interval_group = QButtonGroup(self)
        self.interval_group.setExclusive(True)
        self.sub_interval_group = QButtonGroup(self)
        self.sub_interval_group.setExclusive(True)
        self._interval_buttons: dict[object, QPushButton] = {}
        self._sub_interval_buttons: dict[object, QPushButton] = {}

        def _add_interval_button(target_layout: QHBoxLayout, value: object, label: str) -> None:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("role", "filterButton")
            button.clicked.connect(lambda checked=False, v=value: self._set_interval(v))
            self.interval_group.addButton(button)
            self._interval_buttons[value] = button
            target_layout.addWidget(button)
            if value == self.current_interval:
                button.setChecked(True)

        def _add_sub_interval_button(target_layout: QHBoxLayout, value: object, label: str) -> None:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("role", "filterButton")
            button.clicked.connect(lambda checked=False, v=value: self._set_interval(v))
            self.sub_interval_group.addButton(button)
            self._sub_interval_buttons[value] = button
            target_layout.addWidget(button)
            if value == self.current_interval:
                button.setChecked(True)

        filters = QHBoxLayout()
        filters.addStretch()
        for value, label in (("all", "全樣本"), (5, "5 分(舊制)"), (20, "20 分(舊制)"), (2, "2 分(新制·全部)")):
            _add_interval_button(filters, value, label)
        filters.addStretch()
        layout.addLayout(filters)

        sub_filters = QHBoxLayout()
        sub_filters.addStretch()
        sub_filters_title = QLabel("2分新制細分")
        sub_filters_title.setStyleSheet("color: #888888; font-size: 13px; padding-right: 6px;")
        sub_filters.addWidget(sub_filters_title)
        for key in DURATION_OFFENSE_KEYS:
            _add_sub_interval_button(sub_filters, key, key.replace("_", "·"))
        sub_filters.addStretch()
        layout.addLayout(sub_filters)

        # 如果初始值就是子分桶(理論上不會，current_interval 預設是5)，開頁時也要
        # 讓「2分(新制·全部)」母按鈕同步顯示為已勾選，維持上面說明的視覺一致性。
        if self.current_interval in self._sub_interval_buttons:
            parent_button = self._interval_buttons.get(2)
            if parent_button:
                parent_button.setChecked(True)

        window_filters = QHBoxLayout()
        window_filters.addStretch()
        window_title = QLabel("統計區間")
        window_title.setStyleSheet("color: #888888; font-size: 13px; padding-right: 6px;")
        window_filters.addWidget(window_title)
        self.window_group = QButtonGroup(self)
        self.window_group.setExclusive(True)
        for value, label in WINDOW_FILTERS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("role", "filterButton")
            button.clicked.connect(lambda checked=False, v=value: self._set_window_days(v))
            self.window_group.addButton(button)
            window_filters.addWidget(button)
            if value == self.current_window_days:
                button.setChecked(True)
        window_filters.addStretch()
        layout.addLayout(window_filters)

        self.summary_row = QHBoxLayout()
        self.summary_row.setSpacing(10)
        layout.addLayout(self.summary_row)

        self.status_label = QLabel("準備載入處置統計...")
        self.status_label.setStyleSheet("color: #888888; font-size: 12px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.status_label)

        self.chart_holder = QWidget()
        self.chart_layout = QVBoxLayout(self.chart_holder)
        self.chart_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.chart_holder, stretch=6)

        self.table = QTableWidget()
        self.table.setMinimumHeight(175)
        self.table.setMaximumHeight(240)
        self.table.setStyleSheet(
            """
            QTableWidget {
                background-color: #181818;
                color: white;
                gridline-color: #333333;
                border: 1px solid #2c2c2c;
                border-radius: 6px;
            }
            QHeaderView::section {
                background-color: #242424;
                color: #bbbbbb;
                border: none;
                padding: 5px;
            }
            """
        )
        layout.addWidget(self.table, stretch=2)

    def refresh(self, force_refresh: bool = False) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait(100)

        self.status_label.setText("正在載入處置統計...")
        self.refresh_btn.setEnabled(False)

        self.worker = DisposalStatsOverviewWorker(
            db_path=self.db_path,
            parquet_root=self.parquet_root,
            report_dir=self.report_dir,
            force_refresh=force_refresh,
            window_days=self.current_window_days,
        )
        self.worker.status_message.connect(self.status_label.setText)
        self.worker.finished_data.connect(self._on_loaded)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(lambda *_: self.refresh_btn.setEnabled(True))
        self.worker.start()

    def _set_metric(self, key: str) -> None:
        self.current_metric = key
        self._render_current_view()

    def _set_interval(self, value: int | str) -> None:
        self.current_interval = value

        if value in self._sub_interval_buttons:
            # [Fix 2026-09-04] 點的是子分桶(天數+初犯/累犯)按鈕：這永遠是「2分新制」
            # 底下的子集合，母按鈕要保持勾選狀態，不能被 sub_interval_group 自己的
            # 互斥機制影響(兩組互不干涉，所以這裡要手動同步)。
            parent_button = self._interval_buttons.get(2)
            if parent_button:
                parent_button.setChecked(True)
        else:
            # 點的是主列按鈕(全樣本/5分/20分/2分)：代表離開子分桶檢視，子列的勾選
            # 要清掉，避免畫面同時顯示「2分」跟某個子分桶都是勾選狀態造成混淆。
            checked_sub = self.sub_interval_group.checkedButton()
            if checked_sub:
                self.sub_interval_group.setExclusive(False)
                checked_sub.setChecked(False)
                self.sub_interval_group.setExclusive(True)

        self._render_current_view()

    def _set_window_days(self, value: Optional[int]) -> None:
        if self.current_window_days == value:
            return
        self.current_window_days = value
        self.refresh(force_refresh=value is not None)

    def _on_failed(self, message: str) -> None:
        self.status_label.setText(f"統計載入失敗: {message}")
        self.refresh_btn.setEnabled(True)

    def _on_loaded(self, dataset_obj: object) -> None:
        if not isinstance(dataset_obj, DisposalStatsDataset):
            self.status_label.setText("統計載入失敗: 資料格式不正確")
            self.refresh_btn.setEnabled(True)
            return

        self.dataset = dataset_obj
        self._render_current_view()
        source_text = "快取" if dataset_obj.source == "cache" else "重新計算"
        self.status_label.setText(
            f"統計資料已更新 ({self._range_label()} / {source_text} / {dataset_obj.generated_at.strftime('%Y-%m-%d %H:%M')})"
        )
        self.refresh_btn.setEnabled(True)

    def _render_current_view(self) -> None:
        if self.dataset is None:
            return

        frame = self._current_frame()
        self._render_summary(frame)
        self._render_chart(frame)
        self._render_table(frame)

    def _current_frame(self) -> pd.DataFrame:
        if self.dataset is None:
            return pd.DataFrame()

        if self.current_interval == "all":
            # 「全樣本」只合併 SUPPORTED_INTERVALS(5/20/2 三個互斥母分桶)，
            # 不可以再加入 DURATION_OFFENSE_KEYS——後者是從 2 分母分桶再切出來的
            # 子分桶，同時合併兩者會把 2 分的事件重複加總進去。
            frames = [self.dataset.interval_frame(interval) for interval in SUPPORTED_INTERVALS]
            return self._combine_frames(frames)

        # self.current_interval 在按鈕綁定時就已經是原生型別
        # （int: 5/20/2；str: "5天_初犯"...等），可以直接查表，不需要額外轉型。
        return self.dataset.interval_frame(self.current_interval)

    def _range_label(self) -> str:
        if self.current_window_days is None:
            return "全部"
        if self.current_window_days == 365:
            return "近一年"
        if self.current_window_days == 730:
            return "近二年"
        if self.current_window_days == 1095:
            return "近三年"
        return f"近 {self.current_window_days} 日"

    def _combine_frames(self, frames: list[pd.DataFrame]) -> pd.DataFrame:
        frames = [frame for frame in frames if frame is not None and not frame.empty]
        if not frames:
            return pd.DataFrame()

        merged = pd.concat(frames, ignore_index=True)
        rows = []
        for label in self._ordered_labels(merged["時間點"].dropna().unique().tolist()):
            group = merged[merged["時間點"] == label].copy()
            sample_total = int(group["樣本數"].sum())
            if sample_total <= 0:
                continue

            row = {"時間點": label, "樣本數": sample_total}
            for column in [
                "平均漲跌幅%",
                "平均開收差幅%",
                "上漲機率%",
                "紅K機率%",
                "黑K機率%",
                "平K機率%",
            ]:
                row[column] = round((group[column] * group["樣本數"]).sum() / sample_total, 2)
            rows.append(row)

        return pd.DataFrame(rows)

    def _ordered_labels(self, labels: list[str]) -> list[str]:
        def key(label: str) -> tuple[int, int]:
            if label == "T-1":
                return (0, 0)
            if label.startswith("T"):
                try:
                    return (1, int(label[1:]))
                except ValueError:
                    return (9, 0)
            if label == "Exit":
                return (2, 0)
            if label.startswith("Exit+"):
                try:
                    return (3, int(label[5:]))
                except ValueError:
                    return (9, 0)
            return (9, 0)

        return sorted(labels, key=key)

    def _stage_label(self, label: str) -> str:
        if label == "T-1":
            return "處置前1"
        if label.startswith("T"):
            return f"處置{label[1:]}"
        if label == "Exit":
            return "出關1"
        if label.startswith("Exit+"):
            return f"出關+{label[5:]}"
        return label

    def _render_summary(self, frame: pd.DataFrame) -> None:
        self._clear_layout(self.summary_row)
        cards = self._summary_values(frame)
        for title, value in cards:
            self.summary_row.addWidget(self._summary_card(title, value))

    def _summary_values(self, frame: pd.DataFrame) -> list[tuple[str, str]]:
        title = "全樣本" if self.current_interval == "all" else interval_title(self.current_interval)
        title = f"{title} / {self._range_label()}"
        if frame.empty:
            return [(title, "無資料"), ("T-1 平均漲跌", "-"), ("處置1 平均漲跌", "-"), ("出關1 平均漲跌", "-")]

        sample_total = int(frame["樣本數"].max())

        def value_for(label: str, column: str) -> str:
            hit = frame[frame["時間點"] == label]
            if hit.empty:
                return "-"
            return f"{float(hit.iloc[0][column]):+.2f}%"

        return [
            (title, f"{sample_total:,} 筆樣本"),
            ("T-1 平均漲跌", value_for("T-1", "平均漲跌幅%")),
            ("處置1 平均漲跌", value_for("T1", "平均漲跌幅%")),
            ("出關1 平均漲跌", value_for("Exit", "平均漲跌幅%")),
        ]

    def _summary_card(self, title: str, value: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            """
            QFrame {
                background-color: #191919;
                border: 1px solid #303030;
                border-radius: 8px;
            }
            QLabel#cardTitle {
                color: #888888;
                font-size: 12px;
                border: none;
            }
            QLabel#cardValue {
                color: #ffffff;
                font-size: 20px;
                font-weight: bold;
                border: none;
            }
            """
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        value_label = QLabel(value)
        value_label.setObjectName("cardValue")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return card

    def _render_chart(self, frame: pd.DataFrame) -> None:
        self._clear_layout(self.chart_layout)
        if frame.empty:
            empty = QLabel("沒有可顯示的統計資料")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #888888; font-size: 15px;")
            self.chart_layout.addWidget(empty)
            return

        figure = Figure(figsize=(10.5, 4.8), dpi=100)
        figure.patch.set_facecolor("#1e1e1e")
        ax = figure.add_subplot(111)
        self._style_axis(ax)

        labels = [self._stage_label(label) for label in frame["時間點"].tolist()]
        x_values = list(range(len(labels)))

        if self.current_metric == "prob":
            self._draw_probability_chart(ax, frame, x_values)
        else:
            self._draw_value_chart(ax, frame, x_values)

        ax.set_xticks(x_values)
        ax.set_xticklabels(labels, rotation=0, ha="center", color="#888888")
        figure.tight_layout(pad=1.6)

        self.chart_canvas = FigureCanvasQTAgg(figure)
        self.chart_canvas.setMinimumHeight(385)
        self.chart_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.chart_layout.addWidget(self.chart_canvas)

    def _draw_value_chart(self, ax, frame: pd.DataFrame, x_values: list[int]) -> None:
        metric = METRICS[self.current_metric]
        values = frame[metric["column"]].astype(float).tolist()
        colors = ["#ff3333" if value >= 0 else "#33cc66" for value in values]
        bars = ax.bar(x_values, values, color=colors, width=0.64)
        if values:
            min_value = min(values)
            max_value = max(values)
            padding = max((max_value - min_value) * 0.18, 0.8)
            ax.set_ylim(min(0, min_value) - padding, max(0, max_value) + padding)
        for bar, value in zip(bars, values):
            offset = 0.12 if value >= 0 else -0.12
            vertical_align = "bottom" if value >= 0 else "top"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value + offset,
                f"{value:+.2f}%",
                ha="center",
                va=vertical_align,
                color="#eeeeee",
                fontsize=8,
                fontweight="bold",
            )
        ax.axhline(0, color="#666666", linewidth=1)
        ax.set_title(metric["label"], color="#fcd535", fontsize=16, pad=14)
        ax.set_ylabel(metric["axis"], color="#888888")
        ax.grid(axis="y", color="white", alpha=0.08)

    def _draw_probability_chart(self, ax, frame: pd.DataFrame, x_values: list[int]) -> None:
        black = frame["黑K機率%"].astype(float)
        flat = frame["平K機率%"].astype(float)
        red = frame["紅K機率%"].astype(float)

        ax.bar(x_values, black, color="#33cc66", width=0.64, label="黑K")
        ax.bar(x_values, flat, bottom=black, color="#ffffff", width=0.64, label="平K")
        ax.bar(x_values, red, bottom=black + flat, color="#ff3333", width=0.64, label="紅K")
        for idx, (black_value, flat_value, red_value) in enumerate(zip(black, flat, red)):
            green_y = black_value / 2 if black_value >= 6 else black_value + 1.5
            green_va = "center" if black_value >= 6 else "bottom"
            ax.text(
                idx,
                green_y,
                f"{black_value:.1f}%",
                ha="center",
                va=green_va,
                color="#0f1f14" if black_value >= 6 else "#bff5d1",
                fontsize=8,
                fontweight="bold",
                clip_on=False,
            )

            red_bottom = black_value + flat_value
            red_y = red_bottom + red_value / 2 if red_value >= 6 else min(100, red_bottom + red_value + 1.5)
            red_va = "center" if red_value >= 6 else "bottom"
            ax.text(
                idx,
                red_y,
                f"{red_value:.1f}%",
                ha="center",
                va=red_va,
                color="#ffffff",
                fontsize=8,
                fontweight="bold",
                clip_on=False,
            )
        ax.set_title("紅黑機率", color="#fcd535", fontsize=16, pad=14)
        ax.set_ylabel("機率 (%)", color="#888888")
        ax.set_ylim(0, 100)
        ax.grid(axis="y", color="white", alpha=0.08)
        legend = ax.legend(loc="upper right", frameon=False, ncol=3)
        for text in legend.get_texts():
            text.set_color("#bbbbbb")

    def _style_axis(self, ax) -> None:
        ax.set_facecolor("#1e1e1e")
        ax.tick_params(colors="#888888")
        for spine in ax.spines.values():
            spine.set_color("#333333")
        ax.yaxis.label.set_color("#888888")

    def _render_table(self, frame: pd.DataFrame) -> None:
        columns = list(frame.columns) if not frame.empty else []
        self.table.clear()
        self.table.setColumnCount(len(columns))
        self.table.setRowCount(len(frame))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)

        numeric_columns = {
            "樣本數": 0,
            "平均漲跌幅%": 2,
            "平均開收差幅%": 2,
            "上漲機率%": 1,
            "紅K機率%": 1,
            "黑K機率%": 1,
            "平K機率%": 1,
        }

        for row_idx, (_, row) in enumerate(frame.iterrows()):
            for col_idx, column in enumerate(columns):
                value = row[column]
                if column in numeric_columns:
                    decimals = numeric_columns[column]
                    text = str(int(value)) if column == "樣本數" else f"{float(value):.{decimals}f}"
                else:
                    text = self._stage_label(str(value)) if column == "時間點" else str(value)

                item = QTableWidgetItem(text)
                if column == "時間點":
                    item.setForeground(QBrush(QColor("#fcd535")))
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row_idx, col_idx, item)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
