"""
策略回測頁(2026-11-01 使用者要求)：推播各策略在新制全部處置上的進出場、報酬與績效指標。

計算在 core/strategy_backtest.py(規則直接用推播程式的函式)；本頁只負責顯示。
切到這頁時，資料庫或行情檔有更新就自動重算(背景執行緒，不卡畫面)；也可以按「重新計算」。
"""
import os
from datetime import date

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from PyQt6.QtCore import Qt, QThread, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QColor, QFont  # noqa: E402
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,  # noqa: E402
                             QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser,
                             QVBoxLayout, QWidget)

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei"]
plt.rcParams["axes.unicode_minus"] = False

UP, DOWN, GRAY = "#FF6B6B", "#4CD97B", "#9a9a9a"      # 台股慣例：賺紅、賠綠
_PRICE_PARQUET = r"E:\Vibe Coding\Stock\DB\parquet_data\price_daily_raw"

NOTES = ("回測範圍：新制（2026/08/10 起開始）全部處置，規則與 Telegram 推播同一套程式。報酬已扣手續費（0.1425%×2.8 折）"
         "與證交稅 0.3%；放空未扣融券手續費／借券費。組合：每筆投入「1／最大同時持有數」的資金、不複利、逐日收盤估值，"
         "年化＝總報酬依天數換算（樣本只有約 3 個月，年化只能拿來互相比較）。"
         "5 天初犯預買、7 天累犯預空要在公告前預測，這裡假設預測全中（沒中、隔天賣出／回補的單不在裡面）；"
         "7 天累犯「處置前一天收盤空」10/22 才上線，這裡整段都套現行規則。持有中的單以最新收盤估值（也先扣掉賣出成本）。"
         "成交：收盤漲停買不到 → 隔天開盤買、收盤跌停賣不掉 → 隔天開盤賣（價格後面標「開」）。")


class _Item(QTableWidgetItem):
    """排序用數值(畫面上的文字有 %、逗號時，用 key 比大小)。"""
    def __init__(self, text, key=None, color=None, bold=False):
        super().__init__(text)
        self.key = key
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if color:
            self.setForeground(QColor(color))
        if bold:
            f = QFont()
            f.setBold(True)
            self.setFont(f)

    def __lt__(self, other):
        a, b = self.key, getattr(other, "key", None)
        if a is None or b is None:
            return (a is None, self.text()) < (b is None, other.text())
        return a < b


def _pct(x, digits=2, sign=True):
    if x is None:
        return "—"
    return f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"


def _color(x):
    return None if x is None or abs(x) < 1e-12 else (UP if x > 0 else DOWN)


class BacktestWorker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def run(self):
        try:
            from core import strategy_backtest
            self.done.emit(strategy_backtest.run())
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.failed.emit(str(e))


METRIC_COLS = [
    ("策略", None), ("筆數", "n"), ("已出場", "n_closed"), ("持有中", "n_open"), ("勝率", "win"),
    ("平均報酬", "avg"), ("中位數", "median"), ("最好", "best"), ("最差", "worst"), ("賺賠比", "payoff"),
    ("獲利因子", "pf"), ("平均持有(日)", "hold"), ("組合總報酬", "total"), ("年化報酬", "ann"), ("年化波動", "vol"),
    ("MDD", "mdd"), ("Sharpe", "sharpe"), ("Sortino", "sortino"), ("Calmar", "calmar"),
    ("複利總報酬", "c_total"), ("複利年化", "c_ann"), ("複利MDD", "c_mdd"), ("複利Sharpe", "c_sharpe"),
    ("最大同時持有", "max_conc"), ("目前持有", "now_conc"), ("每筆20萬損益", "pnl"),
    ("前半平均", "first_half"), ("後半平均", "second_half"),
]
BENCH = "0050 買進持有（同期）"
TRADE_COLS = ["策略", "代號", "名稱", "市場", "處置期間", "類型", "方向", "進場日", "進場價", "出場日", "出場價／現價",
              "報酬(扣成本)", "持有(日)", "狀態／出場原因"]
SKIP_COLS = ["策略", "代號", "名稱", "處置期間", "類型", "進場日", "沒做的原因"]


class StrategyBacktestPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.result, self.worker, self._sig = None, None, None
        self._sel = "ALL"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)

        top = QHBoxLayout()
        title = QLabel("策略回測")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #FFFFFF;")
        top.addWidget(title)
        self.info = QLabel("尚未計算")
        self.info.setStyleSheet("color: #fcd535; font-size: 14px; padding-left: 12px;")
        top.addWidget(self.info)
        top.addStretch()
        self.btn = QPushButton("重新計算")
        self.btn.clicked.connect(self.refresh)
        top.addWidget(self.btn)
        lay.addLayout(top)

        notes = QLabel(NOTES)
        notes.setWordWrap(True)
        notes.setStyleSheet(f"color: {GRAY}; font-size: 12px;")
        lay.addWidget(notes)

        split = QSplitter(Qt.Orientation.Vertical)
        self.metrics = QTableWidget(0, len(METRIC_COLS))
        self.metrics.setHorizontalHeaderLabels([c for c, _ in METRIC_COLS])
        self.metrics.verticalHeader().setVisible(False)
        self.metrics.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.metrics.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.metrics.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.metrics.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.metrics.itemSelectionChanged.connect(self._on_select)
        split.addWidget(self.metrics)

        bottom = QSplitter(Qt.Orientation.Horizontal)
        self.fig = Figure(figsize=(5, 3), facecolor="#1E1E1E")
        self.canvas = FigureCanvasQTAgg(self.fig)
        bottom.addWidget(self.canvas)
        self.tabs = QTabWidget()
        self.trades = self._table(TRADE_COLS)
        self.skipped = self._table(SKIP_COLS)
        self.rules = QTextBrowser()
        self.rules.setStyleSheet("font-size: 14px;")
        self.tabs.addTab(self.trades, "進出場明細")
        self.tabs.addTab(self.skipped, "沒做的（濾掉）")
        self.tabs.addTab(self.rules, "策略規則")
        bottom.addWidget(self.tabs)
        bottom.setSizes([450, 900])
        split.addWidget(bottom)
        split.setSizes([260, 520])
        lay.addWidget(split)

    @staticmethod
    def _table(cols):
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        t.setSortingEnabled(True)
        return t

    # ------------------------------------------------------------ 計算
    @staticmethod
    def _signature():
        """資料有沒有更新：處置資料庫、本月行情檔的修改時間＋今天日期。"""
        from core.runtime import get_paths
        sig = [date.today().isoformat()]
        for p in (get_paths().disposal_db,
                  os.path.join(_PRICE_PARQUET, "market=TWSE", f"year={date.today().year}",
                               f"month={date.today().month}", "data.parquet")):
            try:
                sig.append(os.path.getmtime(p))
            except OSError:
                sig.append(None)
        return tuple(sig)

    def showEvent(self, e):
        super().showEvent(e)
        if self._signature() != self._sig:
            self.refresh()

    def refresh(self):
        if self.worker is not None and self.worker.isRunning():
            return
        self._sig = self._signature()
        self.btn.setEnabled(False)
        self.info.setText("計算中…")
        self.worker = BacktestWorker()
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_failed(self, msg):
        self.btn.setEnabled(True)
        self._sig = None
        self.info.setText(f"計算失敗：{msg}")

    def _on_done(self, res):
        self.btn.setEnabled(True)
        self.result = res
        self.info.setText(f"資料截至 {res['as_of']:%Y/%m/%d} 收盤｜新制處置 {res['n_disposals']} 件")
        self._fill_metrics()

    # ------------------------------------------------------------ 顯示
    def _rows(self):
        r = self.result
        out = [(k, v["name"], v["m"], False) for k, v in r["strategies"].items()]
        out += [(c["key"], c["name"], c["m"], True) for c in r["combos"]]
        b = r["combos"][-1]["m"].get("bench")
        if b:
            out.append(("BENCH", BENCH, b, False))
        return out

    def _fill_metrics(self):
        rows = self._rows()
        t = self.metrics
        t.blockSignals(True)
        t.setRowCount(len(rows))
        pick = 0
        for i, (key, name, m, bold) in enumerate(rows):
            if key == self._sel:
                pick = i
            t.setItem(i, 0, _Item(name, bold=bold, color=GRAY if key == "BENCH" else None))
            t.item(i, 0).setData(Qt.ItemDataRole.UserRole, key)
            for j, (_, f) in enumerate(METRIC_COLS[1:], start=1):
                t.setItem(i, j, self._metric_item(f, m.get(f), bold))
            if key == "BENCH":                       # 對照列不能點(沒有交易明細)
                for j in range(t.columnCount()):
                    t.item(i, j).setFlags(Qt.ItemFlag.ItemIsEnabled)
        t.blockSignals(False)
        t.selectRow(pick)
        self._on_select()

    @staticmethod
    def _metric_item(f, v, bold):
        if v is None:
            return _Item("—", None, bold=bold)
        if f in ("n", "n_closed", "n_open", "max_conc", "now_conc"):
            return _Item(f"{v}", v, bold=bold)
        if f == "win":
            return _Item(_pct(v, 0, sign=False), v, bold=bold)
        if f in ("payoff", "pf", "sharpe", "sortino", "calmar", "c_sharpe"):
            return _Item(f"{v:.2f}", v, bold=bold)
        if f == "hold":
            return _Item(f"{v:.1f}", v, bold=bold)
        if f == "pnl":
            return _Item(f"{v:+,.0f}", v, _color(v), bold)
        if f == "vol":
            return _Item(_pct(v, 1, sign=False), v, bold=bold)
        if f in ("mdd", "c_mdd"):
            return _Item(_pct(v, 1), v, DOWN if v < 0 else None, bold)
        if f in ("ann", "c_ann"):
            return _Item(_pct(v, 0), v, _color(v), bold)
        return _Item(_pct(v, 2), v, _color(v), bold)

    def _selected(self):
        r = self.result
        if r is None:
            return None
        row = self.metrics.currentRow()
        it = self.metrics.item(row, 0) if row >= 0 else None
        key = it.data(Qt.ItemDataRole.UserRole) if it else "ALL"
        self._sel = key
        if key in r["strategies"]:
            v = r["strategies"][key]
            return v["name"], v["trades"], v["skipped"], v["m"], [key]
        c = next(c for c in r["combos"] if c["key"] == key)
        skipped = [s for k in c["keys"] for s in r["strategies"][k]["skipped"]]
        return c["name"], c["trades"], skipped, c["m"], list(c["keys"])

    def _on_select(self):
        sel = self._selected()
        if not sel:
            return
        name, trades, skipped, m, keys = sel
        self._fill_trades(trades)
        self._fill_skipped(skipped)
        self._fill_rules(keys)
        self._draw(name, m)
        self.tabs.setTabText(0, f"進出場明細（{len(trades)}）")
        self.tabs.setTabText(1, f"沒做的（濾掉）（{len(skipped)}）")

    def _strategy_name(self, key):
        return self.result["strategies"][key]["name"]

    def _fill_trades(self, trades):
        t = self.trades
        t.setSortingEnabled(False)
        t.setRowCount(len(trades))
        for i, x in enumerate(sorted(trades, key=lambda x: (x["entry"], x["code"]), reverse=True)):
            ret_txt = _pct(x["ret"]) + ("（還原資料可疑）" if x.get("suspicious") else "")
            vals = [
                _Item(self._strategy_name(x["key"])), _Item(x["code"]), _Item(x["name"]), _Item(x["market"]),
                _Item(f"{x['start']:%m/%d}～{x['end']:%m/%d}", x["start"].toordinal()),
                _Item(f"{x['n_days']}天{x['offense']}"),
                _Item(x["side"], color=UP if x["side"] == "多" else DOWN),
                _Item(f"{x['entry']:%Y/%m/%d}", x["entry"].toordinal()),
                _Item(f"{x['entry_px']:g}" + ("（開）" if x.get("entry_mode") == "open" else ""), x["entry_px"]),
                _Item(f"{x['exit']:%Y/%m/%d}" if x["exit"] else "持有中",
                      x["exit"].toordinal() if x["exit"] else 10 ** 7, None if x["exit"] else "#fcd535"),
                _Item(f"{x['exit_px']:g}" + ("（開）" if x.get("exit_mode") == "open" else ""), x["exit_px"]),
                _Item(ret_txt, x["ret"], _color(x["ret"])),
                _Item(f"{len(x['path'])}", len(x["path"])),
                _Item(x["exit_why"], color=None if x["closed"] else "#fcd535"),
            ]
            for j, it in enumerate(vals):
                t.setItem(i, j, it)
        t.setSortingEnabled(True)
        t.sortByColumn(5, Qt.SortOrder.DescendingOrder)
        t.sortByColumn(7, Qt.SortOrder.DescendingOrder)

    def _fill_skipped(self, skipped):
        t = self.skipped
        t.setSortingEnabled(False)
        t.setRowCount(len(skipped))
        for i, s in enumerate(sorted(skipped, key=lambda x: (x["entry"], x["code"]), reverse=True)):
            vals = [_Item(self._strategy_name(s["key"])), _Item(s["code"]), _Item(s["name"]),
                    _Item(f"{s['start']:%m/%d}～{s['end']:%m/%d}", s["start"].toordinal()),
                    _Item(f"{s['n_days']}天{s['offense']}"),
                    _Item(f"{s['entry']:%Y/%m/%d}", s["entry"].toordinal()), _Item(s["why"])]
            for j, it in enumerate(vals):
                t.setItem(i, j, it)
        t.setSortingEnabled(True)

    def _fill_rules(self, keys):
        parts = []
        for k in keys:
            v = self.result["strategies"][k]
            parts.append(f"<p><b style='color:#fcd535'>{v['name']}</b>（{v['side']}）<br>{v['rule']}</p>")
        self.rules.setHtml("".join(parts))

    def _draw(self, name, m):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.set_facecolor("#1E1E1E")
        for s in ax.spines.values():
            s.set_color("#555555")
        ax.tick_params(colors="#CCCCCC", labelsize=8)
        if not m.get("n"):
            ax.set_title(f"{name}：沒有交易", color="#fcd535")
            self.canvas.draw_idle()
            return
        xs = [d for d, _ in m["equity"]]
        ys = [(v - 1) * 100 for _, v in m["equity"]]
        conc = dict(m["conc"])
        ax2 = ax.twinx()
        ax2.bar(xs, [conc.get(d, 0) for d in xs], color="#4da6ff", alpha=0.25, width=0.8)
        ax2.set_ylabel("同時持有筆數", color="#4da6ff", fontsize=8)
        ax2.tick_params(colors="#4da6ff", labelsize=8)
        ax.set_zorder(ax2.get_zorder() + 1)
        ax.patch.set_visible(False)
        ax.plot(xs, ys, color=UP, linewidth=1.8, label="不複利")
        ax.plot(xs, [(v - 1) * 100 for _, v in m["equity_c"]], color="#fcd535", linewidth=1.2, label="複利")
        if m.get("bench_nav"):
            ax.plot(xs, [(v - 1) * 100 for _, v in m["bench_nav"]], color=GRAY, linewidth=1.2, label="0050")
        ax.legend(loc="upper left", fontsize=8, facecolor="#1E1E1E", labelcolor="#CCCCCC", edgecolor="#555555")
        ax.axhline(0, color="#777777", linewidth=0.8)
        ax.set_ylabel("組合累計報酬（%）", color="#CCCCCC", fontsize=8)
        ax.set_title(f"{name}｜總 {m['total'] * 100:+.1f}%　MDD {m['mdd'] * 100:.1f}%　Sharpe {m['sharpe']:.2f}",
                     color="#fcd535", fontsize=11)
        self.fig.autofmt_xdate()
        self.fig.tight_layout()
        self.canvas.draw_idle()
